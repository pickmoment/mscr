from __future__ import annotations

from datetime import date, timedelta

import pytest

from mscr import market_stats
from mscr.db import db_session, init_db


def _insert_instrument(db, ticker: str, name: str, market: str, kind: str = "stock") -> None:
    db.execute(
        "INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,?,?,0,0,'2025-01-01','2026-01-01',0)",
        (ticker, name, kind, market),
    )


def _insert_bars(db, ticker: str, dates: list[str], closes: list[float], halted: set[str] | None = None, nav_map: dict[str, float] | None = None) -> None:
    halted = halted or set()
    nav_map = nav_map or {}
    rows = []
    for day, close in zip(dates, closes):
        if day in halted:
            rows.append((ticker, day, "krx_snapshot", 0, 0, 0, close, 0, 0.0, nav_map.get(day), 1))
        else:
            rows.append((ticker, day, "krx_snapshot", close - 1, close + 1, close - 2, close, 1000, close * 1000, nav_map.get(day), 0))
    db.executemany(
        "INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


def _trading_dates(n: int, start: date = date(2025, 1, 1)) -> list[str]:
    # 주말을 건너뛰어 실제 거래일 캘린더와 비슷하게 만든다.
    out = []
    current = start
    while len(out) < n:
        if current.weekday() < 5:
            out.append(current.isoformat())
        current += timedelta(days=1)
    return out


@pytest.fixture()
def base_db(tmp_path):
    path = tmp_path / "stats.db"
    init_db(path)
    dates = _trading_dates(260)
    with db_session(path) as db:
        _insert_instrument(db, "000001", "상승종목", "KOSPI")
        _insert_instrument(db, "000002", "하락종목", "KOSDAQ")
        flat_closes = [100.0] * 259
        # 마지막 날 큰 폭으로 상승해 신고가를 갱신하게 한다.
        _insert_bars(db, "000001", dates, flat_closes + [130.0])
        # 마지막 날 하락하는 종목.
        _insert_bars(db, "000002", dates, flat_closes + [90.0])
    return path, dates


def test_available_dates_returns_sorted_trading_days(base_db):
    path, dates = base_db
    assert market_stats.available_dates(path) == dates


def test_compute_snaps_to_the_nearest_trading_day(base_db):
    path, dates = base_db
    result = market_stats.compute(dates[-2], path)
    assert result["date"] == dates[-2]
    assert result["requested_date"] == dates[-2]
    future = market_stats.compute("2099-01-01", path)
    assert future["date"] == dates[-1]
    assert future["requested_date"] == "2099-01-01"


def test_compute_reports_breadth_and_new_extremes(base_db):
    path, dates = base_db
    result = market_stats.compute(dates[-1], path)
    assert result["counts"] == {"total": 2, "stock": 2, "etf": 0, "by_market": {"KOSPI": 1, "KOSDAQ": 1, "KONEX": 0}}
    assert result["breadth"]["all"]["up"] == 1
    assert result["breadth"]["all"]["down"] == 1
    assert result["breadth"]["all"]["new_high"] == 1
    assert result["breadth"]["all"]["new_low"] == 1
    assert result["breadth"]["by_market"]["KOSPI"]["up"] == 1
    assert result["breadth"]["by_market"]["KOSDAQ"]["down"] == 1
    gainers = result["rankings"]["gainers_top"]
    assert gainers[0]["ticker"] == "000001"
    assert gainers[0]["change_pct"] == pytest.approx(30.0)
    losers = result["rankings"]["losers_top"]
    assert losers[0]["ticker"] == "000002"


def test_halted_ticker_is_excluded_from_breadth_but_counted_as_halted(tmp_path):
    path = tmp_path / "halted.db"
    init_db(path)
    dates = _trading_dates(30)
    with db_session(path) as db:
        _insert_instrument(db, "000003", "정지종목", "KOSPI")
        _insert_bars(db, "000003", dates, [100.0] * 30, halted={dates[-1]})
    result = market_stats.compute(dates[-1], path)
    assert result["breadth"]["all"]["halted"] == 1
    assert result["breadth"]["all"]["up"] == 0
    assert result["breadth"]["all"]["down"] == 0
    assert result["rankings"]["gainers_top"] == []


def test_market_cap_bands_and_valuation_use_same_day_fundamentals(base_db):
    path, dates = base_db
    resolved = dates[-1].replace("-", "")
    with db_session(path) as db:
        db.execute(
            "INSERT INTO snapshots_fundamental(ticker,date,per,pbr,market_cap,div) VALUES('000001',?,10.0,1.0,2000000000000,2.0)",
            (resolved,),
        )
        db.execute(
            "INSERT INTO snapshots_fundamental(ticker,date,per,pbr,market_cap,div) VALUES('000002',?,20.0,2.0,50000000000,1.0)",
            (resolved,),
        )
    result = market_stats.compute(dates[-1], path)
    bands = {band["category"]: band for band in result["sectors"]}
    assert bands["대형주 (1조원 이상)"]["count"] == 1
    assert bands["소형주 (1천억원 미만)"]["count"] == 1
    assert result["volume"]["market_cap_sum"]["KOSPI"] == pytest.approx(2000000000000)
    assert result["valuation"]["per"]["median"] == pytest.approx(15.0)


def test_etf_breadth_and_nav_premium_are_reported_separately(tmp_path):
    path = tmp_path / "etf.db"
    init_db(path)
    dates = _trading_dates(30)
    with db_session(path) as db:
        _insert_instrument(db, "000001", "일반주식", "KOSPI", kind="stock")
        _insert_bars(db, "000001", dates, [100.0] * 30)
        _insert_instrument(db, "069500", "KODEX 200", "KOSPI", kind="etf")
        _insert_bars(db, "069500", dates, [100.0] * 29 + [110.0], nav_map={dates[-1]: 100.0})
    result = market_stats.compute(dates[-1], path)
    assert result["breadth"]["etf"]["count"] == 1
    assert result["breadth"]["etf"]["up"] == 1
    assert result["breadth"]["all"]["up"] == 1
    assert result["breadth"]["all"]["flat"] == 1
    premium_top = result["etf_rankings"]["premium_top"]
    assert premium_top[0]["ticker"] == "069500"
    assert premium_top[0]["premium_pct"] == pytest.approx(10.0)
    assert result["etf_rankings"]["value_top"][0]["ticker"] == "069500"
    assert all(row["ticker"] != "069500" for row in result["rankings"]["value_top"])
    assert all(row["ticker"] != "069500" for row in result["rankings"]["gainers_top"])


def test_compute_raises_when_no_data_exists(tmp_path):
    path = tmp_path / "empty.db"
    init_db(path)
    with pytest.raises(ValueError):
        market_stats.compute("2025-01-01", path)
