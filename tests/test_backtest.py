from __future__ import annotations

from datetime import date, timedelta

import pytest

from mscr.backtest import _load_bars, _protocol, forward_returns, run
from mscr.db import db_session, init_db
from mscr.indicators import atr

BOX = {"stop_mode": "box", "box_lookback": 5, "box_buffer_atr": 0, "top_n": None}


def day(number: int) -> str:
    return (date(2026, 1, 5) + timedelta(days=number - 1)).isoformat()


def insert_bar(db, ticker, number, o, h, l, c, halted=0):
    db.execute("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (ticker, day(number), "krx_snapshot", o, h, l, c, 1000, 1_000_000, None, halted))


def quiet_bars(db, ticker, first, last, o=100, h=101, l=99, c=100):
    for number in range(first, last + 1):
        insert_bar(db, ticker, number, o, h, l, c)


def add_signal(db, number, ticker, rank=1, screen_id=1, close=100.0):
    db.execute("INSERT INTO screen_signals(screen_id,date,ticker,rank,score,close) VALUES(?,?,?,?,?,?)",
               (screen_id, day(number), ticker, rank, 1.0, close))
    db.execute("INSERT OR IGNORE INTO screen_runs(screen_id,date,matched,ran_at) VALUES(?,?,?,?)", (screen_id, day(number), 1, "2026-02-01T00:00:00"))


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "backtest.db"
    init_db(path)
    with db_session(path) as db:
        db.execute("INSERT INTO screens(id,name,spec,created_at,updated_at) VALUES(1,'박스 조임 후보','{}','2026-01-01','2026-01-01')")
        for ticker, name in (("000001", "가나전자"), ("000002", "다라화학"), ("000003", "마바건설")):
            db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,'stock','KOSPI',0,0,'2026-01-01','2026-03-01',0)", (ticker, name))
    return path


def box_base(db, ticker="000001"):
    """d1~d5는 저가 95로 눌러 20일이 아닌 5일 박스 바닥을 95로 고정한다. 진입가 100, 리스크 5."""
    for number in range(1, 6):
        insert_bar(db, ticker, number, 100, 100, 95, 100)


def test_walk_to_target_scores_target_r_minus_cost(store):
    with db_session(store) as db:
        box_base(db)
        insert_bar(db, "000001", 6, 100, 101, 99, 101)
        insert_bar(db, "000001", 7, 102, 116, 101, 115)
        add_signal(db, 5, "000001")
    result = run(1, BOX, store)
    # 진입 100, 손절 95, 목표 115. 왕복비용 0.25%는 리스크 5원 대비 0.05R.
    assert (result["signals"], result["triggered"], result["trades"]) == (1, 1, 1)
    assert result["expectancy_r"] == pytest.approx(2.95)
    assert result["target_rate"] == 100.0 and result["stop_rate"] == 0.0 and result["timeout_rate"] == 0.0
    assert result["win_rate"] == 100.0 and result["trigger_rate"] == 100.0
    assert result["avg_risk_pct"] == 5.0 and result["avg_days_held"] == 1.0


def test_bar_holding_both_stop_and_target_resolves_as_stop(store):
    with db_session(store) as db:
        box_base(db)
        insert_bar(db, "000001", 6, 100, 101, 99, 100)
        insert_bar(db, "000001", 7, 100, 116, 94, 100)  # 목표 115와 손절 95를 같은 봉이 모두 건드린다
        add_signal(db, 5, "000001")
    result = run(1, BOX, store)
    assert result["trades"] == 1
    assert result["expectancy_r"] == pytest.approx(-1.05)
    assert result["stop_rate"] == 100.0 and result["target_rate"] == 0.0


def test_gap_through_stop_fills_at_open(store):
    with db_session(store) as db:
        box_base(db)
        insert_bar(db, "000001", 6, 100, 101, 99, 100)
        insert_bar(db, "000001", 7, 90, 91, 88, 90)  # 손절 95를 뛰어넘어 시가 90에서 시작
        add_signal(db, 5, "000001")
    result = run(1, BOX, store)
    assert result["expectancy_r"] == pytest.approx(-2.05)
    assert result["expectancy_r"] < -1
    assert result["stop_rate"] == 100.0


def test_untriggered_breakout_counts_as_signal_but_not_as_trade(store):
    with db_session(store) as db:
        box_base(db, "000001")
        quiet_bars(db, "000001", 6, 10, o=99, h=100, l=98, c=99)  # 박스 상단 100 위로 못 간다
        box_base(db, "000002")
        insert_bar(db, "000002", 6, 100, 101, 99, 101)
        insert_bar(db, "000002", 7, 101, 116, 100, 115)
        quiet_bars(db, "000002", 8, 10, o=115, h=116, l=114, c=115)
        add_signal(db, 5, "000001")
        add_signal(db, 5, "000002", rank=2)
    result = run(1, BOX | {"entry": "breakout"}, store)
    assert (result["signals"], result["triggered"], result["trades"]) == (2, 1, 1)
    assert result["trigger_rate"] == 50.0
    # 진입은 d6에 트리거 100.1로 걸린다(시가 100 < 트리거). 손절 95, 리스크 5.1, 목표 115.4를 d7 고가가 넘는다.
    assert result["expectancy_r"] == pytest.approx(3 - 0.0025 * 100.1 / 5.1, abs=5e-5)
    assert result["target_rate"] == 100.0
    assert any("트리거되지 않은 신호" in note for note in result["warnings"])


def test_breakout_gap_above_trigger_enters_at_open(store):
    with db_session(store) as db:
        box_base(db)
        insert_bar(db, "000001", 6, 105, 106, 104, 105)  # 트리거 100.1을 갭으로 건너뛴 시가
        quiet_bars(db, "000001", 7, 10, o=105, h=106, l=104, c=105)
        add_signal(db, 5, "000001")
    result = run(1, BOX | {"entry": "breakout", "horizon_days": 3}, store)
    # 체결가는 트리거 100.1이 아니라 시가 105. 리스크는 105-95=10으로 커진다.
    assert result["avg_risk_pct"] == pytest.approx(10 / 105 * 100, abs=0.005)
    assert result["expectancy_r"] == pytest.approx(-0.0025 * 105 / 10, abs=5e-5)
    assert result["timeout_rate"] == 100.0


def test_non_overlap_suppresses_signal_while_trade_is_open(store):
    with db_session(store) as db:
        box_base(db)
        quiet_bars(db, "000001", 6, 11)
        add_signal(db, 5, "000001")
        add_signal(db, 6, "000001")
    protocol = BOX | {"horizon_days": 3}
    overlapped = run(1, protocol, store)
    assert (overlapped["signals"], overlapped["trades"]) == (1, 1)
    assert any("중복 신호 1건" in note for note in overlapped["warnings"])
    both = run(1, protocol | {"non_overlap": False}, store)
    assert (both["signals"], both["trades"]) == (2, 2)
    assert both["expectancy_r"] == pytest.approx(-0.05)  # 보합 청산이라 왕복비용만 남는다


def test_forward_returns_matches_close_to_close_arithmetic(store):
    with db_session(store) as db:
        quiet_bars(db, "000001", 1, 3, o=100, h=100, l=100, c=100)
        insert_bar(db, "000001", 4, 110, 110, 110, 110)
        insert_bar(db, "000001", 5, 121, 121, 121, 121)
        add_signal(db, 3, "000001")
    result = forward_returns(1, horizons=(1, 2, 5), top_n=None, path=store)
    assert result["signals"] == 1
    horizons = {row["days"]: row for row in result["horizons"]}
    assert horizons[1]["mean_pct"] == pytest.approx(10.0) and horizons[1]["count"] == 1
    assert horizons[2]["mean_pct"] == pytest.approx(21.0) and horizons[2]["win_rate"] == 100.0
    assert horizons[5]["count"] == 0 and horizons[5]["mean_pct"] is None  # 이후 봉이 없다


def test_atr_stop_matches_project_atr(store):
    prices = [100 + (number * 7) % 23 for number in range(40)]
    with db_session(store) as db:
        for offset, close in enumerate(prices, start=1):
            insert_bar(db, "000001", offset, close, close + 3, close - 4, close)
        bars = _load_bars(db, {"000001"}, _protocol({}))
    expected = atr([close + 3 for close in prices], [close - 4 for close in prices], prices, 14)
    assert bars["atr"][:13] == pytest.approx(expected.tolist()[:13], nan_ok=True)
    assert bars["atr"][13:] == pytest.approx(expected.tolist()[13:])


def test_signals_without_bars_are_reported_and_excluded(store):
    with db_session(store) as db:
        box_base(db)
        insert_bar(db, "000001", 6, 100, 101, 99, 100)
        add_signal(db, 5, "000001")
        add_signal(db, 5, "000003", rank=2)  # 일봉이 하나도 없는 종목
    result = run(1, BOX, store)
    assert (result["signals"], result["trades"]) == (1, 1)
    assert any("일봉이 없어 제외한 신호 1건" in note for note in result["warnings"])


def test_period_and_half_split_report_both_halves(store):
    with db_session(store) as db:
        box_base(db)
        quiet_bars(db, "000001", 6, 20)
        for number in (5, 10, 15):
            add_signal(db, number, "000001")
    result = run(1, BOX | {"horizon_days": 2, "non_overlap": False}, store)
    assert result["period"] == {"start": day(5), "end": day(15), "days": 3}
    assert result["trades"] == 3
    assert [row["trades"] for row in result["by_half"]] == [1, 2]
    assert result["total_months"] == 1 and result["profitable_months"] == 0


def test_price_jump_is_not_booked_as_a_stop_out(store):
    with db_session(store) as db:
        box_base(db)
        insert_bar(db, "000001", 6, 100, 101, 99, 100)
        insert_bar(db, "000001", 7, 50, 51, 49, 50)  # 액면병합급 반토막: 시세가 아니라 단절이다
        add_signal(db, 5, "000001")
    result = run(1, BOX, store)
    # 단절 이전 구간에서 마지막 종가(100)로 청산한다. 그대로 통과시키면 -10R짜리 가짜 손절이 잡힌다.
    assert result["expectancy_r"] == pytest.approx(-0.05)
    assert result["timeout_rate"] == 100.0 and result["stop_rate"] == 0.0
    assert any("감자·액면병합" in note for note in result["warnings"])


def test_halted_bar_is_not_tradeable(store):
    with db_session(store) as db:
        box_base(db)
        insert_bar(db, "000001", 6, 0, 0, 0, 100, halted=1)  # 거래정지일은 종가만 들어오고 체결은 불가능하다
        quiet_bars(db, "000001", 7, 9, o=110, h=112, l=108, c=109)
        add_signal(db, 5, "000001")
    result = run(1, BOX | {"horizon_days": 2}, store)
    # 진입은 정지일이 아니라 그다음 거래일 시가 110이다. 손절 95이므로 리스크는 15.
    assert result["trades"] == 1
    assert result["avg_risk_pct"] == pytest.approx(15 / 110 * 100, abs=0.005)
    assert result["expectancy_r"] == pytest.approx((109 - 110) / 15 - 0.0025 * 110 / 15, abs=5e-5)
    assert result["avg_days_held"] == 2.0  # 정지일은 보유일수에도 들어가지 않는다


def test_unknown_protocol_key_is_rejected(store):
    with pytest.raises(ValueError, match="알 수 없는 프로토콜 항목"):
        run(1, {"stop_multiple": 2}, store)
    with pytest.raises(ValueError, match="프리셋을 찾을 수 없습니다"):
        run(99, None, store)
