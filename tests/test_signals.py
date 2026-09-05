from __future__ import annotations

import json

import pytest

from mscr import signals
from mscr.db import db_session, init_db

DAYS = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]
SPEC = {
    "universe": {"kinds": ["stock"], "markets": ["KOSPI"], "exclude_preferred": False, "exclude_spac": False, "exclude_halted": True, "min_bars": 1},
    "formula": "close > 1000",
    "sort": {"formula": "close", "dir": "desc"},
    "limit": 500,
    "as_of_offset": 0,
}


def _instrument(db, ticker: str, name: str) -> None:
    db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,'stock','KOSPI',0,0,?,?,0)", (ticker, name, DAYS[0], DAYS[-1]))


def _bars(db, ticker: str, closes: list[float]) -> None:
    for day, close in zip(DAYS, closes):
        db.execute("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,'krx_snapshot',?,?,?,?,1000,1000000,NULL,0)",
                   (ticker, day, close, close, close, close))


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "signals.db"
    init_db(path)
    with db_session(path) as db:
        _instrument(db, "000001", "가나전자")
        _instrument(db, "000002", "다라화학")
        # 000001은 마지막 이틀만, 000002는 처음 이틀만 조건(종가 1000 초과)을 만족한다.
        _bars(db, "000001", [900, 900, 1100, 1200])
        _bars(db, "000002", [1500, 1400, 800, 700])
        db.execute("INSERT INTO screens(name,spec,created_at,updated_at) VALUES('테스트',?,?,?)", (json.dumps(SPEC), DAYS[0], DAYS[0]))
    return path


def test_capture_records_every_day_including_empty_ones(store):
    result = signals.capture(offsets=range(4), path=store)
    assert result["dates"] == 4
    with db_session(store) as db:
        runs = {row["date"]: row["matched"] for row in db.execute("SELECT date,matched FROM screen_runs").fetchall()}
        rows = db.execute("SELECT date,ticker,rank FROM screen_signals ORDER BY date,rank").fetchall()
    assert runs == {"2026-06-01": 1, "2026-06-02": 1, "2026-06-03": 1, "2026-06-04": 1}
    assert [(row["date"], row["ticker"]) for row in rows] == [
        ("2026-06-01", "000002"), ("2026-06-02", "000002"), ("2026-06-03", "000001"), ("2026-06-04", "000001")]


def test_capture_skips_already_captured_days_unless_forced(store):
    signals.capture(offsets=range(4), path=store)
    again = signals.capture(offsets=range(4), path=store)
    assert again["dates"] == 0 and again["skipped"] == 4
    forced = signals.capture(offsets=[0], force=True, path=store)
    assert forced["dates"] == 1


def test_diff_compares_against_the_previous_captured_day(store):
    signals.capture(offsets=range(4), path=store)
    result = signals.diff(1, path=store)
    assert result["date"] == "2026-06-04" and result["previous"] == "2026-06-03"
    assert [row["ticker"] for row in result["held"]] == ["000001"]
    assert result["entered"] == [] and result["exited"] == []

    switch = signals.diff(1, date="2026-06-03", path=store)
    assert [row["ticker"] for row in switch["entered"]] == ["000001"]
    assert [row["ticker"] for row in switch["exited"]] == ["000002"]
    assert switch["entered"][0]["name"] == "가나전자"


def test_diff_on_a_day_with_no_earlier_capture_reports_no_previous(store):
    signals.capture(offsets=[3], path=store)
    result = signals.diff(1, path=store)
    assert result["previous"] is None and result["exited"] == []
    assert [row["ticker"] for row in result["entered"]] == ["000002"]


def test_streaks_count_consecutive_captured_days(store):
    signals.capture(offsets=range(4), path=store)
    assert signals.streaks(1, path=store)["000001"]["days"] == 2
    assert signals.streaks(1, date="2026-06-03", path=store)["000001"] == {"days": 1, "first_date": "2026-06-03", "truncated": False}
    assert signals.streaks(1, date="2026-06-02", path=store)["000002"]["days"] == 2


def test_coverage_lists_screens_without_a_signal_log(store):
    with db_session(store) as db:
        db.execute("INSERT INTO screens(name,spec,created_at,updated_at) VALUES('빈프리셋',?,?,?)", (json.dumps(SPEC), DAYS[0], DAYS[0]))
    signals.capture(screen_ids=[1], offsets=[0], path=store)
    coverage = {row["name"]: row for row in signals.coverage(store)}
    assert coverage["테스트"]["days"] == 1 and coverage["테스트"]["last_date"] == "2026-06-04"
    assert coverage["빈프리셋"]["days"] == 0 and coverage["빈프리셋"]["first_date"] is None


def test_signal_history_lists_presets_that_matched_a_ticker(store):
    signals.capture(offsets=range(4), path=store)
    history = signals.signal_history("000001", path=store)
    assert [row["date"] for row in history] == ["2026-06-04", "2026-06-03"]
    assert history[0]["name"] == "테스트"
