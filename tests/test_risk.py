from __future__ import annotations

import pytest

from mscr import risk
from mscr.db import db_session, init_db

DAY = "2026-06-04"


def _plan(db, name, ticker, side, quantity, entry, stop, enabled=1):
    db.execute(
        "INSERT INTO trade_plans(name,ticker,side,quantity,order_type,limit_price,entry_price,stop_price,tp1_price,tp1_ratio,tp2_price,tp2_ratio,tp3_trailing_pct,enabled,setup,note,created_at,updated_at)"
        " VALUES(?,?,?,?,'limit',?,?,?,?,0.4,?,0.3,8,?,NULL,NULL,?,?)",
        (name, ticker, side, quantity, entry, entry, stop, entry * 1.3, entry * 1.6, enabled, DAY, DAY))


def _leg(db, plan_id, leg, ticker, side, quantity, price):
    db.execute(
        "INSERT INTO broker_orders(plan_id,leg,as_of,ticker,side,quantity,order_type,limit_price,status,env,broker_order_id,filled_quantity,filled_price,requested_at,updated_at)"
        " VALUES(?,?,?,?,?,?,'market',NULL,'filled','paper',NULL,?,?,?,?)",
        (plan_id, leg, DAY, ticker, side, quantity, quantity, price, DAY, DAY))


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "risk.db"
    init_db(path)
    with db_session(path) as db:
        for ticker, name, close in (("000001", "가나전자", 1200), ("000002", "다라화학", 500)):
            db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,'stock','KOSPI',0,0,?,?,0)", (ticker, name, DAY, DAY))
            db.execute("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,'krx_snapshot',?,?,?,?,1000,1000000,NULL,0)", (ticker, DAY, close, close, close, close))
        db.execute("INSERT INTO trades(ticker,side,trade_date,quantity,price,fee,tax,created_at) VALUES('000001','buy',?,100,1000,0,0,?)", (DAY, DAY))
        db.execute("INSERT INTO trades(ticker,side,trade_date,quantity,price,fee,tax,created_at) VALUES('000002','buy',?,10,600,0,0,?)", (DAY, DAY))
        db.execute("INSERT INTO settings(key,value) VALUES('cash_krw','1000000')")
        _plan(db, "진입대기", "000002", "buy", 20, 600, 550)
        _plan(db, "보유중", "000001", "buy", 100, 1000, 900)
        _leg(db, 2, "entry", "000001", "buy", 100, 1000)
    return path


def test_pending_plan_risks_the_full_planned_distance(store):
    rows = {row["name"]: row for row in risk.heat(store)["plans"]}
    assert rows["진입대기"]["state"] == "pending"
    assert rows["진입대기"]["risk_krw"] == pytest.approx((600 - 550) * 20)


def test_entered_plan_risks_the_distance_from_the_current_price(store):
    rows = {row["name"]: row for row in risk.heat(store)["plans"]}
    # 진입가 1000·손절가 900이지만 현재가가 1200이므로 지금 손절당하면 잃는 금액은 300 × 100주다.
    assert rows["보유중"]["risk_krw"] == pytest.approx((1200 - 900) * 100)
    assert rows["보유중"]["initial_risk_krw"] == pytest.approx((1000 - 900) * 100)


def test_filled_take_profit_leg_shrinks_the_open_risk(store):
    with db_session(store) as db:
        _leg(db, 2, "tp1", "000001", "sell", 40, 1300)
    rows = {row["name"]: row for row in risk.heat(store)["plans"]}
    assert rows["보유중"]["quantity"] == 60
    assert rows["보유중"]["risk_krw"] == pytest.approx((1200 - 900) * 60)


def test_stop_above_the_current_price_carries_no_risk(store):
    with db_session(store) as db:
        db.execute("UPDATE trade_plans SET stop_price=1250 WHERE name='보유중'")
    rows = {row["name"]: row for row in risk.heat(store)["plans"]}
    assert rows["보유중"]["risk_krw"] == 0


def test_disabled_plan_before_entry_is_excluded_from_the_total(store):
    with db_session(store) as db:
        db.execute("UPDATE trade_plans SET enabled=0 WHERE name='진입대기'")
    result = risk.heat(store)
    assert result["pending_risk_krw"] == 0
    assert result["total_risk_krw"] == pytest.approx(result["open_risk_krw"])


def test_heat_is_measured_against_total_assets(store):
    result = risk.heat(store)
    equity = 1_000_000 + 100 * 1200 + 10 * 500
    assert result["equity"] == pytest.approx(equity)
    assert result["total_risk_krw"] == pytest.approx(1000 + 30000)
    assert result["heat_pct"] == pytest.approx(31000 / equity * 100)
    assert result["budget"]["remaining_krw"] == pytest.approx(equity * 0.06 - 31000)
    assert result["budget"]["suggested_max_loss"] == pytest.approx(min(equity * 0.01, equity * 0.06 - 31000))
    assert result["over_limit"] is False


def test_over_limit_when_open_risk_exceeds_the_configured_heat(store):
    risk.save_limits(0.5, 1.0, store)
    result = risk.heat(store)
    assert result["over_limit"] is True
    assert any("히트가 한도를 넘었습니다" in message for message in result["warnings"])
    assert result["budget"]["remaining_krw"] == 0


def test_holdings_without_an_entered_plan_are_reported_unprotected(store):
    result = risk.heat(store)
    assert [row["ticker"] for row in result["unprotected"]] == ["000002"]
    assert any("손절 계획이 없는" in message for message in result["warnings"])


def test_limits_reject_a_per_trade_cap_above_the_portfolio_cap(store):
    with pytest.raises(ValueError):
        risk.save_limits(7, 6, store)
    assert risk.limits(store) == {"risk_per_trade_pct": 1.0, "max_portfolio_heat_pct": 6.0}
