from __future__ import annotations

import json

import pytest

from mscr.brief import build, render, set_tracked_screen_ids, tracked_screen_ids
from mscr.db import db_session, init_db


def insert_instrument(db, ticker, name, market="KOSPI"):
    db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES(?,?,'stock',?,0,0,'2026-01-01','2026-06-02',0)", (ticker, name, market))


def insert_bar(db, ticker, day, o, h, l, c):
    db.execute("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,'krx_snapshot',?,?,?,?,1000,1000000,NULL,0)", (ticker, day, o, h, l, c))


def insert_screen(db, name, screen_id=None):
    cursor = db.execute("INSERT INTO screens(id,name,spec,created_at,updated_at) VALUES(?,?,?,'2026-01-01','2026-01-01')", (screen_id, name, json.dumps({"filters": []})))
    return int(cursor.lastrowid)


def insert_signals(db, screen_id, day, tickers):
    db.execute("INSERT INTO screen_runs(screen_id,date,matched,ran_at) VALUES(?,?,?,'2026-06-02T18:00:00')", (screen_id, day, len(tickers)))
    for rank, (ticker, close) in enumerate(tickers, start=1):
        db.execute("INSERT INTO screen_signals(screen_id,date,ticker,rank,score,close) VALUES(?,?,?,?,?,?)", (screen_id, day, ticker, rank, 1.0 / rank, close))


def insert_plan(db, name, ticker, side="buy", entry=1030, stop=950, quantity=10, enabled=1, setup=None):
    cursor = db.execute(
        "INSERT INTO trade_plans(name,ticker,side,quantity,order_type,limit_price,entry_price,stop_price,tp1_price,tp1_ratio,tp2_price,tp2_ratio,tp3_trailing_pct,enabled,setup,created_at,updated_at) "
        "VALUES(?,?,?,?,'limit',?,?,?,?,0.4,?,0.3,5,?,?,'2026-06-01','2026-06-01')",
        (name, ticker, side, quantity, entry, entry, stop, entry * 1.1, entry * 1.2, enabled, setup))
    return int(cursor.lastrowid)


def fill_entry(db, plan_id, ticker, side, quantity, price, day="2026-06-01"):
    db.execute(
        "INSERT INTO broker_orders(plan_id,leg,as_of,ticker,side,quantity,order_type,limit_price,status,env,broker_order_id,filled_quantity,filled_price,fee,tax,requested_at,updated_at) "
        "VALUES(?,'entry',?,?,?,?,'limit',?,'filled','paper','ORD1',?,?,0,0,?,?)",
        (plan_id, day, ticker, side, quantity, price, quantity, price, f"{day}T09:10:00", f"{day}T09:10:00"))


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "brief.db"
    init_db(path)
    return path


@pytest.fixture()
def market(store):
    with db_session(store) as db:
        insert_instrument(db, "005930", "삼성전자")
        insert_instrument(db, "000660", "SK하이닉스")
        insert_instrument(db, "035720", "카카오", "KOSDAQ")
        for ticker, first, second in (("005930", 1000, 1100), ("000660", 2000, 1900), ("035720", 500, 1000)):
            insert_bar(db, ticker, "2026-06-01", first, first, first, first)
            insert_bar(db, ticker, "2026-06-02", second, second * 1.01, second * 0.99, second)
    return store


def test_screen_section_reports_entered_exited_and_streaks(market):
    with db_session(market) as db:
        screen_id = insert_screen(db, "돌파")
        insert_signals(db, screen_id, "2026-06-01", [("005930", 1000), ("000660", 2000)])
        insert_signals(db, screen_id, "2026-06-02", [("005930", 1100), ("035720", 1000)])

    brief = build(path=market)
    assert brief["as_of"] == "2026-06-02" and brief["previous"] == "2026-06-01"
    screen = brief["screens"][0]
    assert screen["date"] == "2026-06-02" and screen["previous"] == "2026-06-01" and screen["matched"] == 2
    assert screen["stale"] is False
    assert [row["ticker"] for row in screen["entered"]] == ["035720"]
    assert [row["ticker"] for row in screen["exited"]] == ["000660"]
    assert screen["held"] == 1
    entered = screen["entered"][0]
    assert entered["name"] == "카카오" and entered["market"] == "KOSDAQ" and entered["rank"] == 2
    assert entered["close"] == 1000 and entered["change_pct"] == pytest.approx(100.0)
    assert entered["streak_days"] == 1
    exited = screen["exited"][0]
    assert exited["close"] == 1900 and exited["change_pct"] == pytest.approx(-5.0)
    leader = screen["streak_leaders"][0]
    assert leader["ticker"] == "005930" and leader["streak_days"] == 2



def test_tracked_screen_ids_defaults_to_none_and_round_trips(market):
    assert tracked_screen_ids(path=market) is None
    with db_session(market) as db:
        screen_id = insert_screen(db, "돌파")
    set_tracked_screen_ids([screen_id], path=market)
    assert tracked_screen_ids(path=market) == [screen_id]
    set_tracked_screen_ids(None, path=market)
    assert tracked_screen_ids(path=market) is None


def test_brief_only_reports_tracked_presets(market):
    with db_session(market) as db:
        tracked_id = insert_screen(db, "돌파")
        insert_signals(db, tracked_id, "2026-06-02", [("005930", 1100)])
        other_id = insert_screen(db, "역추세")
        insert_signals(db, other_id, "2026-06-02", [("000660", 1900)])

    set_tracked_screen_ids([tracked_id], path=market)
    brief = build(path=market)
    assert brief["tracked_screen_ids"] == [tracked_id]
    assert [screen["screen_id"] for screen in brief["screens"]] == [tracked_id]

    set_tracked_screen_ids(None, path=market)
    brief = build(path=market)
    assert brief["tracked_screen_ids"] is None
    assert {screen["screen_id"] for screen in brief["screens"]} == {tracked_id, other_id}


def test_brief_warns_when_tracked_presets_is_explicitly_empty(market):
    with db_session(market) as db:
        screen_id = insert_screen(db, "돌파")
        insert_signals(db, screen_id, "2026-06-02", [("005930", 1100)])

    set_tracked_screen_ids([], path=market)
    brief = build(path=market)
    assert brief["screens"] == []
    assert any("추적할 프리셋이 없습니다" in warning for warning in brief["warnings"])

def test_screen_without_capture_on_target_day_is_stale_and_warned(market):
    with db_session(market) as db:
        screen_id = insert_screen(db, "돌파")
        insert_signals(db, screen_id, "2026-06-01", [("005930", 1000)])

    brief = build(path=market)
    screen = brief["screens"][0]
    assert screen["date"] == "2026-06-01" and screen["stale"] is True
    assert brief["as_of"] == "2026-06-02"
    assert any("2026-06-01" in warning and "2026-06-02" in warning for warning in brief["warnings"])


def test_explicit_date_uses_that_trading_day(market):
    with db_session(market) as db:
        screen_id = insert_screen(db, "돌파")
        insert_signals(db, screen_id, "2026-06-01", [("005930", 1000)])
        insert_signals(db, screen_id, "2026-06-02", [("035720", 1000)])

    brief = build("2026-06-01", path=market)
    assert brief["as_of"] == "2026-06-01" and brief["previous"] is None
    screen = brief["screens"][0]
    assert screen["date"] == "2026-06-01" and [row["ticker"] for row in screen["entered"]] == ["005930"]
    # 기준일 이후 수집은 보이지 않아야 한다
    assert screen["exited"] == []
    assert screen["entered"][0]["close"] == 1000


def test_waiting_plan_distance_is_signed_and_near_at_three_percent(market):
    with db_session(market) as db:
        insert_plan(db, "경계", "035720", entry=1030)
        insert_plan(db, "먼계획", "035720", entry=1040)
        insert_plan(db, "숏경계", "035720", side="sell", entry=970, stop=1100)

    plans = {plan["name"]: plan for plan in build(path=market)["plans"]}
    assert plans["경계"]["phase"] == "waiting_entry" and plans["경계"]["triggered"] is False
    assert plans["경계"]["close"] == 1000
    assert plans["경계"]["distance_pct"] == pytest.approx(3.0) and plans["경계"]["near"] is True
    assert plans["경계"]["stop_distance_pct"] is None
    assert plans["경계"]["ticker_name"] == "카카오"
    assert plans["먼계획"]["distance_pct"] == pytest.approx(4.0) and plans["먼계획"]["near"] is False
    # 매도 계획은 종가가 진입가보다 높을 때 아직 대기이므로 거리가 양수로 뒤집힌다
    assert plans["숏경계"]["distance_pct"] == pytest.approx(3.0) and plans["숏경계"]["near"] is True


def test_triggered_plan_is_near_even_when_far(market):
    with db_session(market) as db:
        insert_plan(db, "돌파", "035720", entry=600)

    plan = build(path=market)["plans"][0]
    assert plan["triggered"] is True and plan["near"] is True
    assert plan["distance_pct"] == pytest.approx(-40.0)


def test_disabled_and_closed_plans_are_dropped(market):
    with db_session(market) as db:
        insert_plan(db, "비활성", "035720", enabled=0)
        plan_id = insert_plan(db, "청산됨", "005930")
        fill_entry(db, plan_id, "005930", "buy", 10, 1030)
        db.execute("INSERT INTO broker_orders(plan_id,leg,as_of,ticker,side,quantity,order_type,limit_price,status,env,filled_quantity,filled_price,fee,tax,requested_at,updated_at) "
                   "VALUES(?,'stop','2026-06-02','005930','sell',10,'market',NULL,'filled','paper',10,950,0,0,'2026-06-02T10:00:00','2026-06-02T10:00:00')", (plan_id,))

    assert build(path=market)["plans"] == []


def test_positions_flag_holdings_without_a_covering_plan(market):
    with db_session(market) as db:
        db.execute("INSERT INTO trades(ticker,side,trade_date,quantity,price,fee,tax,created_at) VALUES('005930','buy','2026-06-01',10,1000,0,0,'2026-06-01')")
        db.execute("INSERT INTO trades(ticker,side,trade_date,quantity,price,fee,tax,created_at) VALUES('035720','buy','2026-06-01',10,500,0,0,'2026-06-01')")
        plan_id = insert_plan(db, "보유중", "005930", entry=1000, stop=900)
        fill_entry(db, plan_id, "005930", "buy", 10, 1000)

    positions = {row["ticker"]: row for row in build(path=market)["positions"]}
    covered, naked = positions["005930"], positions["035720"]
    assert covered["unprotected"] is False and covered["plan_name"] == "보유중" and covered["stop_price"] == 900
    assert covered["stop_distance_pct"] == pytest.approx((1100 - 900) / 1100 * 100)
    assert naked["unprotected"] is True and naked["plan_name"] is None and naked["stop_distance_pct"] is None
    assert any("손절 계획이 없는" in warning for warning in build(path=market)["warnings"])


def test_watchlist_reports_only_items_that_reached_their_target(market):
    with db_session(market) as db:
        db.execute("INSERT INTO watchlists(id,name,created_at,updated_at) VALUES(1,'관심','2026-06-01','2026-06-01')")
        db.execute("INSERT INTO watchlist_items(watchlist_id,ticker,memo,target_price,added_price,added_at) VALUES(1,'005930',NULL,1050,1000,'2026-06-01')")
        db.execute("INSERT INTO watchlist_items(watchlist_id,ticker,memo,target_price,added_price,added_at) VALUES(1,'000660',NULL,3000,2000,'2026-06-01')")
        db.execute("INSERT INTO watchlist_items(watchlist_id,ticker,memo,target_price,added_price,added_at) VALUES(1,'035720',NULL,NULL,500,'2026-06-01')")

    watch = build(path=market)["watchlist"]
    assert watch["lists"] == 1 and watch["items"] == 3
    assert [row["ticker"] for row in watch["reached"]] == ["005930"]
    reached = watch["reached"][0]
    assert reached["watchlist"] == "관심" and reached["close"] == 1100 and reached["target_price"] == 1050


def test_empty_brief_renders_counts_for_every_section(store):
    brief = build(path=store)
    assert brief["as_of"] is None and brief["screens"] == []
    text = render(brief)
    assert "[기준일 없음] 장 마감 브리핑" in text
    assert "스크리너: 프리셋 0개 · 신규 0건 · 이탈 0건" in text
    assert "계획: 진행 0건 · 임박 0건" in text
    assert "관심종목: 목록 0개 · 종목 0개 · 목표 도달 0건" in text
    assert "보유: 0종목 · 손절 미설정 0종목" in text
    assert any(line.startswith("리스크: 히트") for line in text.splitlines())
    assert any("신호 로그가 비어" in warning for warning in brief["warnings"])
    assert "  - 신호 로그가 비어" in text


def test_render_lists_screen_and_plan_details(market):
    with db_session(market) as db:
        screen_id = insert_screen(db, "돌파")
        insert_signals(db, screen_id, "2026-06-01", [("005930", 1000), ("000660", 2000)])
        insert_signals(db, screen_id, "2026-06-02", [("005930", 1100), ("035720", 1000)])
        insert_plan(db, "경계", "035720", entry=1030)

    text = render(build(path=market))
    assert "[2026-06-02] 장 마감 브리핑 (직전 2026-06-01)" in text
    assert "스크리너: 프리셋 1개 · 신규 1건 · 이탈 1건" in text
    assert "    신규 035720 카카오 2위 1,000 +100.00%" in text
    assert "    이탈 000660 SK하이닉스 1,900 -5.00%" in text
    assert "    연속 005930 삼성전자 2일" in text
    assert "  ! 경계 035720 카카오 · 진입대기 · 현재 1,000 · 진입까지 +3.00%" in text
