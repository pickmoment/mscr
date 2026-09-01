from __future__ import annotations

import pandas as pd
import pytest

from mscr.api import routes
from mscr.db import db_session, init_db


def _call(ticker: str, **overrides):
    kwargs = dict(range="1y", indicators="ma,rsi,macd,bb", ma_periods="5,20,60", rsi_period=14, macd_fast=12, macd_slow=26, macd_signal=9, bb_period=20, bb_k=2.0)
    kwargs.update(overrides)
    return routes.bars(ticker, **kwargs)


def _seed_krx_snapshot(db, ticker: str, dates: list[str]) -> None:
    rows = [(ticker, date, "krx_snapshot", 100.0, 101.0, 99.0, 100.0, 1000, 100000.0, None, 0) for date in dates]
    db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)


def _seed_adjusted(db, ticker: str, dates: list[str], close: float) -> None:
    rows = [(ticker, date, "adjusted", close, close, close, close, 1000, 100000.0, None, 0) for date in dates]
    db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)


@pytest.fixture()
def store(monkeypatch, tmp_path):
    path = tmp_path / "bars.db"
    init_db(path)
    with db_session(path) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('000001','테스트','stock','KOSPI',0,0,'2026-08-01','2026-08-31',0)")
    monkeypatch.setattr(routes, "db_session", lambda *args, **kwargs: db_session(path))
    return path


class _FakeProvider:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.calls = []

    def history(self, ticker, start, end, kind, adjusted=True):
        self.calls.append((ticker, start, end))
        return self.frame


def test_stale_adjusted_cache_is_refreshed_up_to_latest_snapshot_date(store, monkeypatch):
    with db_session(store) as db:
        _seed_krx_snapshot(db, "000001", ["2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31"])
        _seed_adjusted(db, "000001", ["2026-08-27", "2026-08-28"], close=111.0)

    fresh = pd.DataFrame({
        "date": ["2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31"],
        "open": [200.0] * 5, "high": [200.0] * 5, "low": [200.0] * 5, "close": [200.0] * 5, "volume": [500] * 5, "value": [50000.0] * 5, "nav": [None] * 5,
    })
    fake = _FakeProvider(fresh)
    monkeypatch.setattr(routes, "KRXProvider", lambda: fake)

    result = _call("000001")

    assert fake.calls, "stale cache must trigger a live refetch"
    assert result["adjusted"] is True
    assert result["bars"][-1] == {"time": "2026-08-31", "open": 200.0, "high": 200.0, "low": 200.0, "close": 200.0, "volume": 500.0, "halted": False}


def test_fresh_adjusted_cache_skips_live_refetch(store, monkeypatch):
    with db_session(store) as db:
        _seed_krx_snapshot(db, "000001", ["2026-08-27", "2026-08-28"])
        _seed_adjusted(db, "000001", ["2026-08-27", "2026-08-28"], close=111.0)

    def _boom():
        raise AssertionError("should not fetch live data when the cache is already current")
    monkeypatch.setattr(routes, "KRXProvider", _boom)

    result = _call("000001")

    assert result["adjusted"] is True
    assert result["bars"][-1]["close"] == 111.0


def test_halted_bars_are_excluded_from_overlays_but_kept_in_raw_bars(store, monkeypatch):
    with db_session(store) as db:
        rows = []
        for i in range(10):
            close = 100.0 + i
            rows.append(("000001", f"2026-01-{i + 1:02d}", "krx_snapshot", close, close + 1, close - 1, close, 1000, 100000.0, None, 0))
        for date in ("2026-01-11", "2026-01-12", "2026-01-13"):
            rows.append(("000001", date, "krx_snapshot", 0.0, 0.0, 0.0, 109.0, 0, 0.0, None, 1))
        for i, date in enumerate(("2026-01-14", "2026-01-15")):
            close = 110.0 + i
            rows.append(("000001", date, "krx_snapshot", close, close + 1, close - 1, close, 1000, 100000.0, None, 0))
        db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    monkeypatch.setattr(routes, "KRXProvider", lambda: type("EmptyHistoryProvider", (), {"history": staticmethod(lambda *a, **k: pd.DataFrame())})())

    result = _call("000001", ma_periods="3")

    assert result["adjusted"] is False
    assert len(result["bars"]) == 15
    assert any(b["time"] == "2026-01-11" and b["halted"] for b in result["bars"])
    ma3 = {p["time"]: p["value"] for p in result["overlays"]["ma3"]}
    assert not {"2026-01-11", "2026-01-12", "2026-01-13"} & set(ma3)
    assert ma3["2026-01-15"] == pytest.approx((109.0 + 110.0 + 111.0) / 3)


def test_price_jump_flag_surfaces_and_truncates_overlays(store, monkeypatch):
    pre = [(f"2026-01-{d:02d}", 100.0) for d in range(1, 6)]
    post = [("2026-02-01", 1000.0), ("2026-02-02", 950.0), ("2026-02-03", 900.0)]
    with db_session(store) as db:
        rows = [("000001", date, "krx_snapshot", close, close + 1, close - 1, close, 1000, 100000.0, None, 0) for date, close in pre + post]
        db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    monkeypatch.setattr(routes, "KRXProvider", lambda: type("EmptyHistoryProvider", (), {"history": staticmethod(lambda *a, **k: pd.DataFrame())})())

    result = _call("000001", ma_periods="3")

    assert result["price_jump_flag"] is True
    ma3 = {p["time"]: p["value"] for p in result["overlays"]["ma3"]}
    assert "2026-01-05" not in ma3
    assert ma3["2026-02-03"] == pytest.approx((1000.0 + 950.0 + 900.0) / 3)


def test_volume_ma_computed_when_requested(store, monkeypatch):
    with db_session(store) as db:
        rows = [("000001", f"2026-01-{d:02d}", "krx_snapshot", 100.0, 101.0, 99.0, 100.0, 1000.0 + d, 100000.0, None, 0) for d in range(1, 6)]
        db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
    monkeypatch.setattr(routes, "KRXProvider", lambda: type("EmptyHistoryProvider", (), {"history": staticmethod(lambda *a, **k: pd.DataFrame())})())

    result = _call("000001", indicators="volume_ma", volume_ma_period=3)

    volume_ma = {p["time"]: p["value"] for p in result["volume_ma"]}
    assert "2026-01-01" not in volume_ma and "2026-01-02" not in volume_ma
    assert volume_ma["2026-01-03"] == pytest.approx((1001.0 + 1002.0 + 1003.0) / 3)
    assert volume_ma["2026-01-05"] == pytest.approx((1003.0 + 1004.0 + 1005.0) / 3)


def test_volume_ma_absent_when_not_requested(store, monkeypatch):
    with db_session(store) as db:
        _seed_krx_snapshot(db, "000001", ["2026-08-27", "2026-08-28"])
    monkeypatch.setattr(routes, "KRXProvider", lambda: type("EmptyHistoryProvider", (), {"history": staticmethod(lambda *a, **k: pd.DataFrame())})())

    result = _call("000001", indicators="ma")

    assert "volume_ma" not in result
