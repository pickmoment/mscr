from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pandas as pd
import pytest

from mscr.db import db_session, init_db
from mscr.dynamic import calculate_group, evaluate_formula, formula_calls, formula_names, run_screen, ticker_snapshot, validate_formula, weighted_return_score


def test_formula_dsl_calculates_series_and_rejects_code_execution():
    close = pd.Series([10.0, 20.0, 30.0])
    result = evaluate_formula("close / sma(close, 2) - 1", {"close": close})
    assert result.iloc[-1] == pytest.approx(0.2)
    with pytest.raises(ValueError, match="허용되지 않은"):
        validate_formula("__import__('os').system('echo unsafe')")
    condition = evaluate_formula("close > sma(close, 2) and rsi(close, 2) >= 0", {"close": close})
    assert bool(condition.iloc[-1])


def test_multiline_formula_parses_across_all_entry_points():
    formula = "close > sma(close, 2)\nand rsi(close, 2) >= 0\nand close < 1000000"
    close = pd.Series([10.0, 20.0, 30.0])
    validate_formula(formula, {"close"})
    assert formula_names(formula) == {"close"}
    assert formula_calls(formula) == {"sma", "rsi"}
    result = evaluate_formula(formula, {"close": close})
    assert bool(result.iloc[-1])
    custom = [{"key": "custom_trend_ok", "label": "x", "formula": formula, "parameters": []}]
    assert bool(evaluate_formula("custom_trend_ok()", {"close": close}, custom).iloc[-1])


def test_dynamic_screen_calculates_custom_indicator_from_bars(tmp_path):
    path = tmp_path / "screen.db"
    init_db(path)
    start = date(2025, 1, 1)
    bars = []
    for index in range(260):
        close = 100.0 + index
        bars.append(("000001", (start + timedelta(days=index)).isoformat(), "krx_snapshot", close, close + 1, close - 1, close, 1000 + index, close * 1000, None, 0))
    with db_session(path) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('000001','테스트','stock','KOSPI',0,0,'2025-01-01','2025-09-17',0)")
        db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", bars)
        db.execute("INSERT INTO indicator_definitions(key,label,unit,formula,parameters,enabled,created_at,updated_at) VALUES('custom_ma_gap','MA 이격','ratio','close / sma(close, period) - 1','[{\"name\":\"period\",\"default\":20,\"min\":1,\"max\":500,\"integer\":true}]',1,'2025-01-01','2025-01-01')")
    spec = {
        "universe": {"kinds": ["stock"], "markets": ["KOSPI"], "min_bars": 250},
        "formula": "custom_ma_gap(20) > 0 and close > sma(close, 20)",
        "sort": {"formula": "custom_ma_gap(20)", "dir": "desc"},
        "limit": 10,
    }
    rows = run_screen(spec, path)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "000001"
    assert math.isfinite(rows[0]["_sort"])


def test_ticker_snapshot_as_of_offset_rewinds_to_an_earlier_valid_bar(tmp_path):
    path = tmp_path / "offset.db"
    init_db(path)
    with db_session(path) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('000001','테스트','stock','KOSPI',0,0,'2026-01-01','2026-01-05',0)")
        bars = [("000001", f"2026-01-{day:02d}", "krx_snapshot", 100.0 + day, 101.0 + day, 99.0 + day, 100.0 + day, 1000, 100000.0, None, 0) for day in range(1, 6)]
        db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", bars)

    assert ticker_snapshot("000001", path)["as_of"] == "2026-01-05"
    assert ticker_snapshot("000001", path, as_of_offset=1)["as_of"] == "2026-01-04"
    assert ticker_snapshot("000001", path, as_of_offset=2)["close"] == 103.0
    assert ticker_snapshot("000001", path, as_of_offset=10) == {}


def test_run_screen_as_of_offset_shifts_the_matched_bar(tmp_path):
    path = tmp_path / "offset_screen.db"
    init_db(path)
    start = date(2025, 1, 1)
    bars = [("000001", (start + timedelta(days=index)).isoformat(), "krx_snapshot", 100.0 + index, 101.0 + index, 99.0 + index, 100.0 + index, 1000, 100000.0, None, 0) for index in range(260)]
    with db_session(path) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('000001','테스트','stock','KOSPI',0,0,'2025-01-01','2025-09-17',0)")
        db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", bars)
    base_spec = {"universe": {"kinds": ["stock"], "markets": ["KOSPI"], "min_bars": 0}, "formula": "close == 359", "sort": {"formula": "close"}, "limit": 10}

    assert [row["close"] for row in run_screen(base_spec, path)] == [359.0]
    assert run_screen(base_spec | {"as_of_offset": 1}, path) == []
    assert [row["close"] for row in run_screen(base_spec | {"formula": "close == 358", "as_of_offset": 1}, path)] == [358.0]

    with pytest.raises(ValueError, match="as_of_offset"):
        run_screen(base_spec | {"as_of_offset": -1}, path)


def _definition(key: str, formula: str, parameters: str = '[{"name":"period","default":20,"min":1,"max":500,"integer":true}]') -> dict:
    return {"key": key, "label": key, "unit": "ratio", "formula": formula, "parameters": json.loads(parameters), "enabled": True}


def test_definition_can_read_snapshot_scalars_not_only_series():
    env = {"close": pd.Series([10.0, 11.0, 12.0]), "change_pct": 3.5, "per": 8.0}
    custom = [_definition("custom_cheap_mover", "change_pct / per", "[]")]
    result = evaluate_formula("custom_cheap_mover()", env, custom)
    assert result.iloc[-1] == pytest.approx(3.5 / 8.0)


def test_custom_indicator_can_call_another_custom_indicator():
    env = {"close": pd.Series([10.0, 20.0, 30.0])}
    custom = [
        _definition("custom_gap", "close / sma(close, period) - 1"),
        _definition("custom_gap_x2", "custom_gap(period) * 2"),
    ]
    inner = evaluate_formula("custom_gap(2)", env, custom)
    outer = evaluate_formula("custom_gap_x2(2)", env, custom)
    assert outer.iloc[-1] == pytest.approx(inner.iloc[-1] * 2)


def test_caller_parameters_do_not_leak_into_the_called_definition():
    env = {"close": pd.Series([10.0, 20.0, 30.0])}
    custom = [
        _definition("custom_uses_secret", "close * secret", "[]"),
        _definition("custom_outer", "custom_uses_secret()", '[{"name":"secret","default":3,"min":1,"max":9,"integer":true}]'),
    ]
    with pytest.raises(ValueError, match="secret"):
        evaluate_formula("custom_outer(3)", env, custom)


def test_circular_custom_indicators_are_rejected_instead_of_recursing():
    env = {"close": pd.Series([10.0, 20.0, 30.0])}
    custom = [_definition("custom_a", "custom_b(period)"), _definition("custom_b", "custom_a(period)")]
    with pytest.raises(ValueError, match="순환 참조"):
        evaluate_formula("custom_a(2)", env, custom)
    itself = [_definition("custom_loop", "custom_loop(period) + 1")]
    with pytest.raises(ValueError, match="순환 참조"):
        evaluate_formula("custom_loop(2)", env, itself)


def test_nested_custom_indicator_runs_through_a_screen(tmp_path):
    path = tmp_path / "nested.db"
    init_db(path)
    start = date(2025, 1, 1)
    bars = [("000001", (start + timedelta(days=index)).isoformat(), "krx_snapshot", 100.0 + index, 101.0 + index, 99.0 + index, 100.0 + index, 1000, 100000.0, None, 0) for index in range(260)]
    with db_session(path) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('000001','테스트','stock','KOSPI',0,0,'2025-01-01','2025-09-17',0)")
        db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", bars)
        for key, formula in (("custom_gap", "close / sma(close, period) - 1"), ("custom_gap_up", "custom_gap(period) > 0")):
            db.execute("INSERT INTO indicator_definitions(key,label,unit,formula,parameters,enabled,created_at,updated_at) VALUES(?,?,'ratio',?,'[{\"name\":\"period\",\"default\":20,\"min\":1,\"max\":500,\"integer\":true}]',1,'2025-01-01','2025-01-01')", (key, key, formula))
    rows = run_screen({"universe": {"kinds": ["stock"], "markets": ["KOSPI"], "min_bars": 250}, "formula": "custom_gap_up(20)", "sort": {"formula": "custom_gap(20)", "dir": "desc"}, "limit": 10}, path)
    assert [row["ticker"] for row in rows] == ["000001"]


def test_custom_indicator_cannot_shadow_a_builtin_name():
    """사용자 지표는 내장 함수보다 먼저 조회되므로 이름이 겹치면 조용히 가로챈다. 그걸 막는다."""
    env = {"close": pd.Series([10.0, 20.0, 30.0])}
    assert evaluate_formula("abs(-5)", env).iloc[-1] == 5.0
    fake_abs = [_definition("abs", "value * 0 + 12345", '[{"name":"value","default":1,"min":null,"max":null,"integer":false}]')]
    with pytest.raises(ValueError, match="내장 이름을 덮어씁니다"):
        evaluate_formula("abs(-5)", env, fake_abs)
    with pytest.raises(ValueError, match="내장 이름을 덮어씁니다"):
        evaluate_formula("close", env, [_definition("close", "1", "[]")])


def test_indicator_key_without_prefix_works_end_to_end():
    env = {"close": pd.Series([10.0, 20.0, 30.0]), "change_pct": 4.0}
    custom = [_definition("ma_gap", "close / sma(close, period) - 1"), _definition("gap_vs_change", "ma_gap(period) - change_pct / 100")]
    inner = evaluate_formula("ma_gap(2)", env, custom).iloc[-1]
    outer = evaluate_formula("gap_vs_change(2)", env, custom).iloc[-1]
    assert outer == pytest.approx(inner - 0.04)


def test_as_of_matches_the_priced_bar_when_the_latest_bar_is_halted():
    frame = pd.DataFrame([
        {"date": "2026-08-27", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0, "value": 100000.0, "halted": 0},
        {"date": "2026-08-28", "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.0, "volume": 1000.0, "value": 100000.0, "halted": 0},
        {"date": "2026-08-31", "open": 0.0, "high": 0.0, "low": 0.0, "close": 102.0, "volume": 0.0, "value": 0.0, "halted": 1},
    ])

    result = calculate_group(frame)

    assert result["as_of"] == "2026-08-28"
    assert result["close"] == 102.0
    assert result["halted"] is True


def test_unadjusted_price_jump_truncates_history_before_the_break():
    """감자·액면병합 등으로 미수정 종가가 하루 만에 KRX 일일 가격제한폭(±30%)을 넘게 뛰면,
    그 이전 구간은 이평선 등 추세 지표 계산에서 제외돼야 한다 (052420 실사례 재현)."""
    frame = pd.DataFrame(
        [{"date": f"2026-01-{day:02d}", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0, "value": 100000.0, "halted": 0} for day in range(1, 6)]
        + [
            {"date": "2026-02-01", "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1000.0, "volume": 1000.0, "value": 100000.0, "halted": 0},
            {"date": "2026-02-02", "open": 1000.0, "high": 1000.0, "low": 950.0, "close": 950.0, "volume": 1000.0, "value": 100000.0, "halted": 0},
            {"date": "2026-02-03", "open": 950.0, "high": 950.0, "low": 900.0, "close": 900.0, "volume": 1000.0, "value": 100000.0, "halted": 0},
        ]
    )

    result = calculate_group(frame, sort_expression="sma(close, 3)")

    assert result["price_jump_flag"] is True
    assert result["bars_available"] == 3
    assert result["close"] == 900.0
    assert result["as_of"] == "2026-02-03"
    assert result["_sort"] == pytest.approx((1000.0 + 950.0 + 900.0) / 3)


def test_normal_history_without_a_split_keeps_price_jump_flag_false():
    frame = pd.DataFrame([{"date": f"2026-01-{day:02d}", "open": 100.0 + day, "high": 101.0 + day, "low": 99.0 + day, "close": 100.0 + day, "volume": 1000.0, "value": 100000.0, "halted": 0} for day in range(1, 11)])

    result = calculate_group(frame)

    assert result["price_jump_flag"] is False
    assert result["bars_available"] == 10


def _growth_frame(ticker: str, days: int, daily_growth: float) -> pd.DataFrame:
    start = date(2024, 1, 1)
    return pd.DataFrame([
        {"date": (start + timedelta(days=i)).isoformat(), "open": 100.0 * (1 + daily_growth) ** i, "high": 100.0 * (1 + daily_growth) ** i, "low": 100.0 * (1 + daily_growth) ** i, "close": 100.0 * (1 + daily_growth) ** i, "volume": 1000.0, "value": 100000.0, "halted": 0}
        for i in range(days)
    ])


def test_weighted_return_score_needs_a_full_year_of_valid_bars():
    assert weighted_return_score(_growth_frame("x", 252, 0.001)) is None
    assert weighted_return_score(_growth_frame("x", 253, 0.001)) is not None


def test_weighted_return_score_matches_weighted_quarter_returns():
    frame = _growth_frame("x", 253, 0.001)

    score = weighted_return_score(frame)

    expected = (0.4 * (1.001 ** 63 - 1) + 0.2 * (1.001 ** 126 - 1) + 0.2 * (1.001 ** 189 - 1) + 0.2 * (1.001 ** 252 - 1)) * 100
    assert score == pytest.approx(expected)


def test_run_screen_reports_weighted_return_as_a_raw_score_not_a_rank(tmp_path):
    path = tmp_path / "weighted_return.db"
    init_db(path)
    with db_session(path) as db:
        for ticker, name, growth in (("000001", "약세", -0.002), ("000002", "중간", 0.0), ("000003", "강세", 0.004)):
            db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,'stock','KOSPI',0,0,'2024-01-01','2024-09-10',0)", (ticker, name))
            frame = _growth_frame(ticker, 260, growth)
            rows = [(ticker, row["date"], "krx_snapshot", row["open"], row["high"], row["low"], row["close"], row["volume"], row["value"], None, 0) for row in frame.to_dict("records")]
            db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('000004','기록부족','stock','KOSPI',0,0,'2024-01-01','2024-09-10',0)")
        short_frame = _growth_frame("000004", 100, 0.01)
        rows = [("000004", row["date"], "krx_snapshot", row["open"], row["high"], row["low"], row["close"], row["volume"], row["value"], None, 0) for row in short_frame.to_dict("records")]
        db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)

    rows = run_screen({"universe": {"kinds": ["stock"], "markets": ["KOSPI"], "min_bars": 0}, "formula": "close > 0", "sort": {"formula": "close"}, "limit": 10}, path)
    weighted_return_by_ticker = {row["ticker"]: row["weighted_return"] for row in rows}

    assert weighted_return_by_ticker["000004"] is None
    assert weighted_return_by_ticker["000001"] < weighted_return_by_ticker["000002"] < weighted_return_by_ticker["000003"]
    assert weighted_return_by_ticker["000001"] < 0 < weighted_return_by_ticker["000003"]
    assert weighted_return_by_ticker["000002"] == pytest.approx(0.0, abs=1e-9)
    assert weighted_return_by_ticker["000003"] == pytest.approx(weighted_return_score(_growth_frame("000003", 260, 0.004)))


def test_ticker_snapshot_exposes_weighted_return_without_a_universe(tmp_path):
    path = tmp_path / "single.db"
    init_db(path)
    with db_session(path) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('000001','테스트','stock','KOSPI',0,0,'2024-01-01','2024-09-10',0)")
        frame = _growth_frame("000001", 260, 0.003)
        rows = [("000001", row["date"], "krx_snapshot", row["open"], row["high"], row["low"], row["close"], row["volume"], row["value"], None, 0) for row in frame.to_dict("records")]
        db.executemany("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)

    result = ticker_snapshot("000001", path)

    assert result["weighted_return"] == pytest.approx(weighted_return_score(frame))
