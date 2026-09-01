import pandas as pd
import pytest

from mscr.db import db_session, init_db
from mscr.ingest import latest_trading_day, run_ingest


def _seed_instrument(path, ticker: str, kind: str = "stock") -> None:
    with db_session(path) as db:
        db.execute(
            "INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,?,?,0,0,'2026-01-01','2026-08-28',0)",
            (ticker, ticker, kind, "KOSPI"),
        )


def _fake_fdr_snapshot(day="2026-08-31"):
    frame = pd.DataFrame({
        "ticker": ["005930", "999999"],
        "open": [249000, 1000],
        "high": [260000, 1100],
        "low": [246000, 900],
        "close": [260000, 1050],
        "volume": [17009810, 500],
        "value": [4647038997556, 500000],
    })
    return day, frame


def test_fdr_source_stores_bars_only_for_known_stock_tickers(tmp_path, monkeypatch):
    path = tmp_path / "ingest.db"
    init_db(path)
    _seed_instrument(path, "005930")
    monkeypatch.setattr("mscr.ingest.fdr_stock_snapshot", lambda: _fake_fdr_snapshot())

    run_ingest(source="fdr", path=path)

    with db_session(path) as db:
        rows = db.execute("SELECT ticker,date,source,close FROM daily_bars").fetchall()
        run = db.execute("SELECT status, rows FROM ingest_runs WHERE date='2026-08-31' AND kind='stock'").fetchone()
    assert [dict(row) for row in rows] == [{"ticker": "005930", "date": "2026-08-31", "source": "krx_snapshot", "close": 260000.0}]
    assert dict(run) == {"status": "ok", "rows": 1}


def test_fdr_source_skips_already_done_day_without_force(tmp_path, monkeypatch):
    path = tmp_path / "ingest.db"
    init_db(path)
    _seed_instrument(path, "005930")
    monkeypatch.setattr("mscr.ingest.fdr_stock_snapshot", lambda: _fake_fdr_snapshot())

    run_ingest(source="fdr", path=path)
    with db_session(path) as db:
        db.execute("UPDATE daily_bars SET close=999 WHERE ticker='005930'")

    run_ingest(source="fdr", path=path)

    with db_session(path) as db:
        close = db.execute("SELECT close FROM daily_bars WHERE ticker='005930'").fetchone()[0]
    assert close == 999


def test_fdr_source_forces_refetch(tmp_path, monkeypatch):
    path = tmp_path / "ingest.db"
    init_db(path)
    _seed_instrument(path, "005930")
    monkeypatch.setattr("mscr.ingest.fdr_stock_snapshot", lambda: _fake_fdr_snapshot())

    run_ingest(source="fdr", path=path)
    with db_session(path) as db:
        db.execute("UPDATE daily_bars SET close=999 WHERE ticker='005930'")

    run_ingest(source="fdr", force=True, path=path)

    with db_session(path) as db:
        close = db.execute("SELECT close FROM daily_bars WHERE ticker='005930'").fetchone()[0]
    assert close == 260000.0


def test_fdr_source_ignores_tickers_outside_known_universe(tmp_path, monkeypatch):
    path = tmp_path / "ingest.db"
    init_db(path)
    _seed_instrument(path, "005930")
    monkeypatch.setattr("mscr.ingest.fdr_stock_snapshot", lambda: _fake_fdr_snapshot())

    run_ingest(source="fdr", path=path)

    with db_session(path) as db:
        tickers = {row[0] for row in db.execute("SELECT ticker FROM daily_bars").fetchall()}
    assert tickers == {"005930"}


def test_fdr_source_requires_seeded_universe(tmp_path, monkeypatch):
    path = tmp_path / "ingest.db"
    init_db(path)
    monkeypatch.setattr("mscr.ingest.fdr_stock_snapshot", lambda: _fake_fdr_snapshot())

    with pytest.raises(RuntimeError, match="유니버스가 비어"):
        run_ingest(source="fdr", path=path)


def test_invalid_source_is_rejected(tmp_path):
    path = tmp_path / "ingest.db"
    with pytest.raises(ValueError, match="지원하지 않는 소스"):
        run_ingest(source="bogus", path=path)


class _FakeProvider:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def latest_trading_day(self):
        if self.error:
            raise self.error
        return self.value

def test_latest_trading_day_normalizes_compact_provider_date():
    assert latest_trading_day(_FakeProvider(value="20260828")) == "2026-08-28"

def test_latest_trading_day_wraps_provider_failure():
    with pytest.raises(RuntimeError, match="KRX 최근 거래일 조회에 실패했습니다"):
        latest_trading_day(_FakeProvider(error=RuntimeError("boom")))
