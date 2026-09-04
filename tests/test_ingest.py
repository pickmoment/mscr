from datetime import date

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


class _FakeAlphaSquareProvider:
    def __init__(self, db, delay=None):
        self.db = db

    def history(self, tickers, start, end):
        return pd.DataFrame(
            [{"ticker": "005930", "date": end, "open": 100, "high": 110, "low": 90, "close": 105, "volume": 10}],
            columns=["ticker", "date", "open", "high", "low", "close", "volume"],
        )


class _RecordingAlphaSquareProvider:
    calls: list[tuple[str, str]] = []

    def __init__(self, db, delay=None):
        self.db = db

    def history(self, tickers, start, end):
        _RecordingAlphaSquareProvider.calls.append((start, end))
        return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume"])


def test_alphasquare_source_days_one_covers_a_single_calendar_day_window(tmp_path, monkeypatch):
    """bdate_range는 양 끝을 포함하므로 --days 1이 이틀치(오늘+어제)로 새는 걸 막는 회귀 테스트다."""
    path = tmp_path / "ingest.db"
    init_db(path)
    _seed_instrument(path, "005930")
    monkeypatch.setattr("mscr.ingest._today", lambda: date(2026, 9, 3))  # 목요일
    _RecordingAlphaSquareProvider.calls = []
    monkeypatch.setattr("mscr.providers.alphasquare.AlphaSquareProvider", _RecordingAlphaSquareProvider)

    run_ingest(source="alphasquare", days=1, path=path)

    assert _RecordingAlphaSquareProvider.calls == [("2026-09-03", "2026-09-03")]


def test_alphasquare_source_backfills_local_universe_tickers_one_by_one(tmp_path, monkeypatch):
    path = tmp_path / "ingest.db"
    init_db(path)
    _seed_instrument(path, "005930")
    _seed_instrument(path, "999999")
    monkeypatch.setattr("mscr.ingest._today", lambda: date(2026, 8, 31))
    monkeypatch.setattr("mscr.providers.alphasquare.AlphaSquareProvider", _FakeAlphaSquareProvider)

    # days=1은 2026-08-31(월)이 유일한 영업일인 창을 만들어 폴백 대상을 그 하루로 고정한다.
    run_ingest(source="alphasquare", days=1, path=path)

    with db_session(path) as db:
        close = db.execute("SELECT close FROM daily_bars WHERE ticker='005930' AND date='2026-08-31'").fetchone()[0]
        run = db.execute("SELECT status, rows FROM ingest_runs WHERE date='2026-08-31' AND kind='stock'").fetchone()
    assert close == 105.0
    assert dict(run) == {"status": "ok", "rows": 1}


def test_alphasquare_source_ignores_stale_local_krx_anchor_and_uses_todays_date(tmp_path, monkeypatch):
    """로컬 daily_bars의 최신 krx_snapshot 날짜가 KRX 공표 지연으로 뒤처져 있어도,
    alpha-square는 그 날짜가 아니라 오늘 날짜를 기준으로 공백을 메운다."""
    path = tmp_path / "ingest.db"
    init_db(path)
    _seed_instrument(path, "005930")
    with db_session(path) as db:
        db.execute("INSERT INTO daily_bars(ticker,date,source,close) VALUES('005930','2026-08-20','krx_snapshot',999)")
    monkeypatch.setattr("mscr.ingest._today", lambda: date(2026, 8, 31))
    monkeypatch.setattr("mscr.providers.alphasquare.AlphaSquareProvider", _FakeAlphaSquareProvider)

    run_ingest(source="alphasquare", days=1, path=path)

    with db_session(path) as db:
        close = db.execute("SELECT close FROM daily_bars WHERE ticker='005930' AND date='2026-08-31'").fetchone()[0]
    assert close == 105.0


def test_alphasquare_source_skips_days_already_marked_done(tmp_path, monkeypatch):
    path = tmp_path / "ingest.db"
    init_db(path)
    _seed_instrument(path, "005930")
    with db_session(path) as db:
        db.execute("INSERT INTO daily_bars(ticker,date,source,close) VALUES('005930','2026-08-31','krx_snapshot',260000)")
        db.execute("INSERT INTO ingest_runs(date,kind,status,rows,elapsed_ms,ran_at) VALUES('2026-08-31','stock','ok',1,10,datetime('now'))")
    monkeypatch.setattr("mscr.ingest._today", lambda: date(2026, 8, 31))
    monkeypatch.setattr("mscr.providers.alphasquare.AlphaSquareProvider", _FakeAlphaSquareProvider)

    run_ingest(source="alphasquare", days=1, path=path)

    with db_session(path) as db:
        close = db.execute("SELECT close FROM daily_bars WHERE ticker='005930' AND date='2026-08-31'").fetchone()[0]
    assert close == 260000.0


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


class _RecordingKRXProvider:
    """trading_days에 전달된 (start, end)만 기록하고, 나머지는 빈 결과로 흘려보낸다."""

    def __init__(self, as_of: str):
        self.as_of = as_of
        self.trading_days_calls: list[tuple] = []

    def latest_trading_day(self):
        return self.as_of

    def trading_days(self, start, end):
        self.trading_days_calls.append((start, end))
        return []

    def stock_universe(self, as_of):
        return pd.DataFrame(columns=["ticker", "name", "market"])

    def etf_universe(self, as_of):
        return pd.DataFrame(columns=["ticker", "name", "category"])

    def cap_snapshot(self, as_of):
        return pd.DataFrame(columns=["ticker", "close", "market_cap", "shares", "volume", "value"])

    def fundamental_snapshot(self, as_of):
        return pd.DataFrame(columns=["ticker", "bps", "per", "pbr", "eps", "div", "dps"])


def test_krx_source_days_one_covers_a_single_calendar_day_window(tmp_path):
    """trading_days도 양 끝을 포함하므로 --days 1이 이틀치로 새는 걸 막는 회귀 테스트다."""
    path = tmp_path / "ingest.db"
    provider = _RecordingKRXProvider("20260903")

    run_ingest(source="krx", days=1, provider=provider, path=path)

    assert provider.trading_days_calls == [(date(2026, 9, 3), date(2026, 9, 3))]


def test_krx_source_days_seven_covers_a_six_day_lookback(tmp_path):
    path = tmp_path / "ingest.db"
    provider = _RecordingKRXProvider("20260903")

    run_ingest(source="krx", days=7, provider=provider, path=path)

    assert provider.trading_days_calls == [(date(2026, 8, 28), date(2026, 9, 3))]
