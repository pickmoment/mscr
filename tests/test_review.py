from __future__ import annotations

import pytest

from mscr.db import db_session, init_db
from mscr.review import plan_results, stats


def insert_bar(db, day, o, h, l, c, ticker="005930"):
    db.execute("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (ticker, day, "krx_snapshot", o, h, l, c, 1000, 1_000_000, None, 0))


def insert_plan(db, name, *, ticker="005930", side="buy", quantity=10, entry_price=1000, stop_price=900, tp1_price=1100, tp1_ratio=0.4, tp2_price=1200, tp2_ratio=0.3, setup=None):
    cursor = db.execute(
        "INSERT INTO trade_plans(name,ticker,side,quantity,order_type,limit_price,entry_price,stop_price,tp1_price,tp1_ratio,tp2_price,tp2_ratio,tp3_trailing_pct,enabled,setup,note,created_at,updated_at)"
        " VALUES(?,?,?,?,'limit',NULL,?,?,?,?,?,?,5,1,?,NULL,'2026-06-01','2026-06-01')",
        (name, ticker, side, quantity, entry_price, stop_price, tp1_price, tp1_ratio, tp2_price, tp2_ratio, setup),
    )
    return int(cursor.lastrowid)


def insert_order(db, plan_id, leg, day, quantity, price, *, side="buy", status="filled", fee=0.0, tax=0.0, filled=None, ticker="005930"):
    filled_quantity = quantity if filled is None else filled
    db.execute(
        "INSERT INTO broker_orders(plan_id,leg,as_of,ticker,side,quantity,order_type,limit_price,status,env,filled_quantity,filled_price,fee,tax,requested_at,updated_at)"
        " VALUES(?,?,?,?,?,?,'limit',NULL,?,'paper',?,?,?,?,?,?)",
        (plan_id, leg, day, ticker, side, quantity, status, filled_quantity, price, fee, tax, f"{day}T09:00:00", f"{day}T09:05:00"),
    )


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "review.db"
    init_db(path)
    with db_session(path) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('005930','삼성전자','stock','KOSPI',0,0,'2026-01-01','2026-08-31',0)")
    return path


def by_plan(path):
    return {row["plan_id"]: row for row in plan_results(path)}


def test_full_size_exit_scores_exactly_one_r_and_stop_out_minus_one_r(store):
    with db_session(store) as db:
        insert_bar(db, "2026-06-02", 1000, 1010, 990, 1000)
        insert_bar(db, "2026-06-03", 1000, 1110, 890, 1100)
        winner = insert_plan(db, "전량청산")
        loser = insert_plan(db, "손절")
        for plan_id in (winner, loser):
            insert_order(db, plan_id, "entry", "2026-06-02", 10, 1000)
        insert_order(db, winner, "trailing", "2026-06-03", 10, 1100, side="sell")
        insert_order(db, loser, "stop", "2026-06-03", 10, 900, side="sell")
    rows = by_plan(store)
    won, lost = rows[winner], rows[loser]
    assert (won["status"], won["realized_r"], won["total_r"]) == ("closed", 1.0, 1.0)
    assert (won["realized_krw"], won["open_r"], won["open_quantity"]) == (1000.0, None, 0.0)
    assert (won["exit_date"], won["days_held"], won["r_unit"]) == ("2026-06-03", 2, 100.0)
    assert (lost["realized_r"], lost["realized_krw"], lost["status"]) == (-1.0, -1000.0, "closed")
    assert [row["plan_id"] for row in plan_results(store)] == sorted([winner, loser], reverse=True)  # 진입일이 같으면 최근 계획 먼저


def test_partial_exit_marks_remaining_quantity_to_latest_close(store):
    with db_session(store) as db:
        insert_bar(db, "2026-06-02", 1000, 1010, 990, 1000)
        insert_bar(db, "2026-06-03", 1050, 1120, 1040, 1100)
        insert_bar(db, "2026-06-04", 1150, 1210, 1140, 1200)
        plan_id = insert_plan(db, "절반익절")
        insert_order(db, plan_id, "entry", "2026-06-02", 10, 1000)
        insert_order(db, plan_id, "tp1", "2026-06-03", 4, 1100, side="sell")
    row = by_plan(store)[plan_id]
    assert row["status"] == "open"
    assert (row["open_quantity"], row["open_r"]) == (6.0, pytest.approx(1.2))  # 종가 1200, 잔량 6주 / (100원 x 10주)
    assert (row["realized_r"], row["total_r"]) == (pytest.approx(0.4), pytest.approx(1.6))
    assert row["exits"][0]["realized_r"] == 1.0  # 레그 단위 R은 그 물량 기준
    assert (row["exit_date"], row["days_held"]) == ("2026-06-03", 3)  # 마지막 청산일은 기록하되 경로 구간은 최신 봉까지


def test_excursions_only_use_bars_from_entry_onwards(store):
    with db_session(store) as db:
        insert_bar(db, "2026-06-01", 900, 2000, 500, 1000)  # 진입 전 봉은 무시되어야 한다
        insert_bar(db, "2026-06-02", 1000, 1010, 990, 1000)
        insert_bar(db, "2026-06-03", 1000, 1250, 950, 1200)
        plan_id = insert_plan(db, "보유중")
        insert_order(db, plan_id, "entry", "2026-06-02", 10, 1000)
    row = by_plan(store)[plan_id]
    assert (row["mfe_r"], row["mae_r"]) == (2.5, -0.5)
    assert row["days_held"] == 2


def test_entry_slippage_is_positive_when_fill_is_worse_than_plan(store):
    with db_session(store) as db:
        insert_bar(db, "2026-06-02", 1000, 1010, 990, 1000)
        insert_bar(db, "2026-06-03", 1050, 1120, 1040, 1100)
        long_plan = insert_plan(db, "추격매수")
        short_plan = insert_plan(db, "공매도", side="sell", stop_price=1100, tp1_price=900, tp2_price=800)
        insert_order(db, long_plan, "entry", "2026-06-02", 10, 1010)
        insert_order(db, short_plan, "entry", "2026-06-02", 10, 990, side="sell")
        insert_order(db, short_plan, "stop", "2026-06-03", 10, 1100)
    rows = by_plan(store)
    assert rows[long_plan]["entry_slippage_pct"] == pytest.approx(1.0)  # 계획보다 비싸게 산 매수
    assert rows[short_plan]["entry_slippage_pct"] == pytest.approx(1.0)  # 계획보다 싸게 판 매도도 불리 = 양수
    assert rows[short_plan]["realized_r"] == pytest.approx(-1.1)  # 990에 팔아 1100에 되샀다


def test_split_entry_fills_average_price_and_costs_reduce_r(store):
    with db_session(store) as db:
        for day, price in (("2026-06-02", 1000), ("2026-06-03", 1050), ("2026-06-04", 1130)):
            insert_bar(db, day, price, price + 10, price - 10, price)
        plan_id = insert_plan(db, "분할진입")
        insert_order(db, plan_id, "entry", "2026-06-02", 4, 1000, fee=100)
        insert_order(db, plan_id, "entry", "2026-06-03", 6, 1050, fee=150)
        insert_order(db, plan_id, "trailing", "2026-06-04", 10, 1130, side="sell", fee=50)
    row = by_plan(store)[plan_id]
    assert (row["entry_price"], row["entry_quantity"], row["entry_date"]) == (1030.0, 10.0, "2026-06-02")
    assert (row["realized_krw"], row["realized_r"]) == (700.0, pytest.approx(0.7))  # (1130-1030)*10 - 수수료 300
    assert not any("수수료" in message for message in stats(store)["warnings"])


def test_unfilled_and_rejected_orders_are_not_settled(store):
    with db_session(store) as db:
        insert_bar(db, "2026-06-02", 1000, 1010, 990, 1000)
        insert_bar(db, "2026-06-03", 1000, 1010, 890, 900)
        pending = insert_plan(db, "대기중")
        entered = insert_plan(db, "진입완료")
        insert_order(db, pending, "entry", "2026-06-02", 10, 1000, status="dry_run", filled=0)
        insert_order(db, entered, "entry", "2026-06-02", 10, 1000)
        insert_order(db, entered, "stop", "2026-06-03", 10, 900, side="sell", status="rejected", filled=0)
    rows = by_plan(store)
    assert pending not in rows  # 진입 체결이 없는 계획은 결산 대상이 아니다
    assert (rows[entered]["exits"], rows[entered]["status"]) == ([], "open")
    assert rows[entered]["realized_krw"] == 0.0


def test_stats_profit_factor_is_none_without_losing_trades(store):
    with db_session(store) as db:
        for day in ("2026-06-02", "2026-06-03", "2026-07-02", "2026-07-03"):
            insert_bar(db, day, 1000, 1110, 990, 1100)
        first = insert_plan(db, "6월승", setup="돌파")
        second = insert_plan(db, "7월승")
        insert_order(db, first, "entry", "2026-06-02", 10, 1000)
        insert_order(db, first, "trailing", "2026-06-03", 10, 1100, side="sell")
        insert_order(db, second, "entry", "2026-07-02", 10, 1000)
        insert_order(db, second, "trailing", "2026-07-03", 10, 1050, side="sell")
    result = stats(store)
    assert result["profit_factor"] is None and result["avg_loss_r"] is None
    assert (result["trades"], result["wins"], result["losses"], result["win_rate"]) == (2, 2, 0, 100.0)
    assert (result["total_r"], result["avg_r"], result["expectancy_r"]) == (pytest.approx(1.5), pytest.approx(0.75), pytest.approx(0.75))
    assert result["max_drawdown_r"] == 0 and result["max_consecutive_losses"] == 0
    assert [group["setup"] for group in result["by_setup"]] == ["돌파", "(미분류)"]
    assert [group["month"] for group in result["by_month"]] == ["2026-06", "2026-07"]
    assert result["equity_curve"] == [{"date": "2026-06-03", "cumulative_r": 1.0}, {"date": "2026-07-03", "cumulative_r": pytest.approx(1.5)}]
    assert any("20건" in message for message in result["warnings"]) and any("수수료" in message for message in result["warnings"])


def test_stats_tracks_consecutive_losses_and_drawdown(store):
    outcomes = [("2026-06-03", 1100), ("2026-06-04", 900), ("2026-06-05", 900), ("2026-06-08", 1100), ("2026-06-09", 900)]
    with db_session(store) as db:
        insert_bar(db, "2026-06-02", 1000, 1010, 990, 1000)
        for index, (day, exit_price) in enumerate(outcomes):
            insert_bar(db, day, 1000, 1110, 890, exit_price)
            plan_id = insert_plan(db, f"계획{index}", setup="돌파" if exit_price > 1000 else None)
            insert_order(db, plan_id, "entry", "2026-06-02", 10, 1000)
            leg = "trailing" if exit_price > 1000 else "stop"
            insert_order(db, plan_id, leg, day, 10, exit_price, side="sell")
    result = stats(store)
    assert (result["trades"], result["wins"], result["losses"]) == (5, 2, 3)
    assert result["max_consecutive_losses"] == 2  # +1, -1, -1, +1, -1
    assert result["max_drawdown_r"] == -2.0  # 누적 +1 고점에서 -1까지
    assert result["profit_factor"] == pytest.approx(2 / 3)
    assert (result["win_rate"], result["total_r"]) == (40.0, pytest.approx(-1.0))
    assert [(group["setup"], group["trades"], group["total_r"]) for group in result["by_setup"]] == [("돌파", 2, 2.0), ("(미분류)", 3, -3.0)]
    assert result["open"] == {"count": 0, "total_open_r": 0}
