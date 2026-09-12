from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest

from mscr import guard
from mscr.broker.kis import Fill, OrderResult
from mscr.broker.stream import parse_frame
from mscr.db import db_session, init_db
from mscr.live import Daemon, daemon_status, market_phase, panic, write_state
from mscr.trading import evaluate_plans, list_orders, manage_open_orders, run_plans, save_plan, sync_orders


class LiveBroker:
    """주문·정정·취소·현재가를 모두 흉내 내는 스텁. 주문한 수량 그대로 체결된다."""

    account_masked = "1234****-01"

    def __init__(self, env: str = "paper", prices: dict | None = None, fill: bool = True):
        self.env = env
        self.prices = prices or {}
        self.fill = fill
        self.submitted: list[tuple] = []
        self.amended: list[tuple] = []
        self._next = 1
        self._orders: dict[str, dict] = {}

    def submit_order(self, ticker, side, quantity, order_type, limit_price):
        self.submitted.append((ticker, side, quantity, order_type, limit_price))
        order_id = f"ID{self._next:05d}"
        self._next += 1
        self._orders[order_id] = {"quantity": quantity, "price": limit_price or self.prices.get(ticker, {}).get("price", 1000.0), "filled": quantity if self.fill else 0.0}
        return OrderResult(order_id, "submitted", "정상 접수", {"rt_cd": "0"}, org_no="91252")

    def order_fill(self, broker_order_id, order_date=None):
        order = self._orders[broker_order_id]
        filled = order["filled"]
        status = "filled" if filled >= order["quantity"] else "partial" if filled > 0 else "submitted"
        return Fill(filled, order["price"], 0.0, 0.0, status, {"rt_cd": "0"}, remaining=order["quantity"] - filled)

    def cancel_order(self, org_no, broker_order_id, quantity=0.0, all_quantity=True):
        self.amended.append(("cancel", broker_order_id, quantity))
        return OrderResult(broker_order_id, "submitted", "취소 접수", {"rt_cd": "0"}, org_no=org_no)

    def revise_order(self, org_no, broker_order_id, quantity, order_type, limit_price, all_quantity=True):
        self.amended.append(("revise", broker_order_id, quantity))
        order_id = f"ID{self._next:05d}"
        self._next += 1
        self._orders[order_id] = {"quantity": quantity, "price": self.prices.get("price", 1000.0), "filled": quantity}
        return OrderResult(order_id, "submitted", "정정 접수", {"rt_cd": "0"}, org_no=org_no)

    def current_price(self, ticker):
        return dict(self.prices[ticker], ticker=ticker)


def insert_bar(db, ticker, day, o, h, l, c):
    db.execute("INSERT INTO daily_bars(ticker,date,source,open,high,low,close,volume,value,nav,halted) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (ticker, day, "krx_snapshot", o, h, l, c, 1000, 1_000_000, None, 0))


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "live.db"
    init_db(path)
    with db_session(path) as db:
        db.execute("INSERT INTO instruments(ticker,name,kind,market,is_preferred,is_spac,first_seen,last_seen,delisted) VALUES('005930','삼성전자','stock','KOSPI',0,0,'2026-01-01','2026-08-31',0)")
        insert_bar(db, "005930", "2026-06-01", 1000, 1010, 990, 1000)
    return path


def plan_payload(**overrides):
    return {"name": "돌파매수", "ticker": "005930", "side": "buy", "quantity": 10, "order_type": "limit", "limit_price": 1300,
            "entry_price": 1005, "stop_price": 950, "tp1_price": 1100, "tp1_ratio": 0.4, "tp2_price": 1200, "tp2_ratio": 0.3,
            "tp3_trailing_pct": 5, "enabled": True} | overrides


def quote(price, high=None, low=None, halted=False):
    return {"price": price, "open": price, "high": high if high is not None else price, "low": low if low is not None else price,
            "volume": 1000, "halted": halted, "received_at": datetime.now().isoformat(timespec="seconds")}


# -- 시세 파싱 ---------------------------------------------------------------

def test_tick_frame_splits_multiple_quotes_and_ignores_foreign_tr():
    fields = ["005930", "093000", "71000", "2", "100", "0.1", "70900", "70500", "71500", "70200", "71100", "70900", "10", "12345", "999"]
    fields += ["0"] * 18 + ["20260912", "20", "N"] + ["0"] * 5
    raw = "0|H0STCNT0|001|" + "^".join(fields)
    tick = parse_frame(raw)[0]
    assert (tick["ticker"], tick["price"], tick["high"], tick["low"], tick["halted"]) == ("005930", 71000.0, 71500.0, 70200.0, False)
    assert parse_frame("0|H0STASP0|001|005930^1^2") == []


# -- 실시간 판정 -------------------------------------------------------------

def test_live_quote_enters_on_current_price_not_on_a_spike_that_already_passed(store):
    save_plan(plan_payload(), store)
    # 고가는 진입가를 넘었지만 현재가는 그 아래로 되돌아왔다 — 지나간 자리를 추격하지 않는다.
    passed = evaluate_plans(path=store, quotes={"005930": quote(1001, high=1020)})[0]
    assert (passed["next_leg"], passed["triggered"], passed["live"]) == ("entry", False, True)
    now = evaluate_plans(path=store, quotes={"005930": quote(1006, high=1020)})[0]
    assert (now["next_leg"], now["triggered"]) == ("entry", True)


def test_stop_uses_session_low_so_a_gap_the_daemon_missed_still_fires(store):
    save_plan(plan_payload(), store)
    broker = LiveBroker()
    run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(1006)}, origin="daemon")
    sync_orders(broker=broker, path=store)
    # 지금은 손절가 위로 올라왔지만 그사이 저가가 손절가를 뚫었다 — 반드시 잡아야 한다.
    recovered = evaluate_plans(path=store, quotes={"005930": quote(1000, low=940)})[0]
    assert (recovered["next_leg"], recovered["triggered"]) == ("stop", True)
    assert recovered["order_quantity"] == pytest.approx(10.0)


def test_halted_ticker_is_never_ordered(store):
    save_plan(plan_payload(), store)
    orders = run_plans(broker=LiveBroker(), dry_run=False, path=store, quotes={"005930": quote(1006, halted=True)})
    assert (orders[0]["status"], "거래정지" in orders[0]["message"]) == ("skipped", True)


# -- 실체결 기준 수량 --------------------------------------------------------

def test_partial_entry_rescales_take_profit_legs_to_what_actually_filled(store):
    save_plan(plan_payload(), store)
    broker = LiveBroker(fill=False)
    entry = run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(1006)})[0]
    broker._orders[entry["broker_order_id"]]["filled"] = 7.0  # 10주 중 7주만 체결
    sync_orders(broker=broker, path=store)
    with db_session(store) as db:
        db.execute("INSERT INTO trades(ticker,side,trade_date,quantity,price,fee,tax,created_at) VALUES('005930','buy','2026-06-02',7,1006,0,0,'2026-06-02')")
    tp1 = evaluate_plans(path=store, quotes={"005930": quote(1105)})[0]
    assert tp1["next_leg"] == "tp1"
    assert tp1["order_quantity"] == pytest.approx(3.0)  # 7주 기준 배분(3/2/2), 계획 수량 기준 4주가 아니다


# -- 안전장치 ---------------------------------------------------------------

def test_kill_switch_blocks_entries_but_never_blocks_exits(store):
    save_plan(plan_payload(), store)
    broker = LiveBroker()
    run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(1006)})
    sync_orders(broker=broker, path=store)
    with db_session(store) as db:
        db.execute("INSERT INTO trades(ticker,side,trade_date,quantity,price,fee,tax,created_at) VALUES('005930','buy','2026-06-02',10,1006,0,0,'2026-06-02')")
    guard.engage("변동성 급등", store)

    save_plan(plan_payload(name="두번째", entry_price=1005), store)
    blocked = [order for order in run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(1006)}) if order["leg"] == "entry"]
    assert blocked and blocked[0]["status"] == "skipped" and "킬스위치" in blocked[0]["message"]

    exits = [order for order in run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(940, low=940)}) if order["leg"] == "stop"]
    assert exits and exits[0]["status"] == "submitted"


def test_daily_entry_limit_stops_new_entries_only(store):
    save_plan(plan_payload(), store)
    guard.save(store, trade_daily_entry_limit=1, trade_require_paper_first=0)
    broker = LiveBroker()
    first = run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(1006)})
    assert first[0]["status"] == "submitted"
    save_plan(plan_payload(name="두번째"), store)
    second = [order for order in run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(1006)}) if order["plan_name"] == "두번째"]
    assert second and second[0]["status"] == "skipped" and "한도" in second[0]["message"]


def test_real_account_requires_a_paper_fill_first_and_paper_progress_stays_isolated(store):
    plan_id = save_plan(plan_payload(), store)
    real = LiveBroker(env="real")
    blocked = run_plans(broker=real, dry_run=False, path=store, quotes={"005930": quote(1006)})
    assert blocked[0]["status"] == "skipped" and "모의계좌 선행 검증" in blocked[0]["message"]

    paper = LiveBroker(env="paper")
    run_plans(broker=paper, dry_run=False, path=store, quotes={"005930": quote(1006)})
    sync_orders(broker=paper, path=store)
    assert plan_id in guard.verified_plans(store)

    # 모의에서 진입이 끝났어도 실계좌 판정은 여전히 "진입 전"이어야 한다.
    assert evaluate_plans(path=store, env="real", quotes={"005930": quote(1006)})[0]["next_leg"] == "entry"
    allowed = run_plans(broker=real, dry_run=False, path=store, quotes={"005930": quote(1006)})
    assert allowed[0]["status"] == "submitted"


def test_panic_engages_kill_switch_and_cancels_resting_orders(store):
    save_plan(plan_payload(), store)
    broker = LiveBroker(fill=False)
    run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(1006)})
    result = panic(broker=broker, reason="장애", path=store)
    assert result["guard"]["trade_kill_switch"] == 1
    assert [action for action, *_ in broker.amended] == ["cancel"]
    assert list_orders(path=store)[0]["status"] == "cancelled"


# -- 미체결 정리 -------------------------------------------------------------

def test_unfilled_entry_is_cancelled_and_not_re_entered(store):
    save_plan(plan_payload(), store)
    broker = LiveBroker(fill=False)
    run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(1006)})
    handled = manage_open_orders(broker=broker, path=store, timeout_sec=0.0)
    assert handled[0]["status"] == "cancelled"
    # 취소된 진입은 같은 자리에서 되풀이되지 않는다.
    again = run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(1006)})
    assert again == []


def test_unfilled_exit_is_repriced_to_market_instead_of_cancelled(store):
    save_plan(plan_payload(), store)
    broker = LiveBroker()
    run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(1006)})
    sync_orders(broker=broker, path=store)
    with db_session(store) as db:
        db.execute("INSERT INTO trades(ticker,side,trade_date,quantity,price,fee,tax,created_at) VALUES('005930','buy','2026-06-02',10,1006,0,0,'2026-06-02')")
    broker.fill = False
    run_plans(broker=broker, dry_run=False, path=store, quotes={"005930": quote(940, low=940)})
    handled = manage_open_orders(broker=broker, path=store, timeout_sec=0.0)
    assert [action for action, *_ in broker.amended] == ["revise"]
    assert [order["status"] for order in handled] == ["cancelled", "submitted"]
    assert handled[1]["order_type"] == "market" and handled[1]["replaces_order_id"] == handled[0]["id"]


# -- 데몬 -------------------------------------------------------------------

def test_market_phase_marks_weekends_and_the_entry_cutoff():
    assert market_phase(datetime(2026, 9, 12, 10, 0)) == "holiday"   # 토요일
    assert market_phase(datetime(2026, 9, 11, 8, 59)) == "before"
    assert market_phase(datetime(2026, 9, 11, 9, 0)) == "open"
    assert market_phase(datetime(2026, 9, 11, 15, 16)) == "closing"
    assert market_phase(datetime(2026, 9, 11, 15, 30)) == "after"


def test_daemon_recovers_missed_trigger_on_startup_then_records_heartbeat(store):
    save_plan(plan_payload(), store)
    guard.save(store, trade_require_paper_first=0)
    broker = LiveBroker(prices={"005930": quote(1006)})
    daemon = Daemon(broker, dry_run=False, path=store, use_stream=False, max_cycles=1,
                    now=lambda: datetime(2026, 9, 11, 10, 0), heartbeat_interval=0)
    result = daemon.run()
    assert result["orders"] == 1
    assert broker.submitted == [("005930", "buy", 10.0, "limit", 1300.0)]
    state = daemon_status(store)
    assert (state["status"], state["mode"], state["stream"]) == ("stopped", "live", "off")
    assert state["orders"] == 1


def test_daemon_waits_before_the_open_and_exits_after_the_close(store):
    save_plan(plan_payload(), store)
    broker = LiveBroker(prices={"005930": quote(1006)})
    after = Daemon(broker, dry_run=False, path=store, use_stream=False, now=lambda: datetime(2026, 9, 11, 16, 0))
    assert after.run()["reason"] == "market_closed"
    assert broker.submitted == []
    before = Daemon(broker, dry_run=False, path=store, use_stream=False, wait_for_open=False, now=lambda: datetime(2026, 9, 11, 8, 0))
    assert before.run()["reason"] == "before"
    assert broker.submitted == []


def test_daemon_refuses_to_start_live_while_the_kill_switch_is_up(store):
    from mscr.live import run_daemon
    guard.engage("점검 중", store)
    with pytest.raises(ValueError, match="킬스위치"):
        run_daemon(broker=LiveBroker(), dry_run=False, path=store)


def test_dead_daemon_shows_up_as_stale(store):
    write_state(store, status="running", heartbeat_at=(datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds"))
    state = daemon_status(store)
    assert (state["alive"], state["stale"]) == (False, True)


def test_stop_event_ends_the_loop(store):
    save_plan(plan_payload(), store)
    stop = threading.Event()
    stop.set()
    daemon = Daemon(LiveBroker(prices={"005930": quote(1006)}), dry_run=True, path=store, use_stream=False,
                    stop_event=stop, now=lambda: datetime(2026, 9, 11, 10, 0))
    assert daemon.run()["reason"] == "stopped"


# -- 웹소켓 클라이언트 -------------------------------------------------------

class _StubForStream:
    """로컬 에코 서버를 KIS 대신 물리기 위한 최소 브로커."""

    def __init__(self, url: str):
        self.env, self._url = "paper", url

    @property
    def ws_url(self) -> str:
        return self._url

    def approval_key(self) -> str:
        return "TEST-APPROVAL"


def _tick_frame(ticker: str, price: float) -> str:
    fields = [ticker, "093000", str(price), "2", "100", "0.1", str(price), str(price), str(price + 10), str(price - 10), "0", "0", "10", "12345", "999"]
    fields += ["0"] * 18 + ["20260912", "20", "N"] + ["0"] * 5
    return "0|H0STCNT0|001|" + "^".join(fields)


def test_quote_stream_subscribes_answers_pings_and_delivers_ticks():
    import asyncio
    import json as jsonlib

    import websockets

    from mscr.broker.stream import QuoteStream

    received: list[dict] = []
    ready = threading.Event()
    holder: dict = {}

    async def handler(connection):
        async for raw in connection:
            message = jsonlib.loads(raw)
            received.append(message)
            ticker = message["body"]["input"]["tr_key"]
            if message["header"]["tr_type"] == "1":
                await connection.send(jsonlib.dumps({"header": {"tr_id": "PINGPONG"}}))
                await connection.send(_tick_frame(ticker, 71000))

    async def serve():
        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            holder["port"] = server.sockets[0].getsockname()[1]
            ready.set()
            await holder["stop"]

    loop = asyncio.new_event_loop()

    def run_server():
        asyncio.set_event_loop(loop)
        holder["stop"] = loop.create_future()
        loop.run_until_complete(serve())

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    assert ready.wait(5)
    stream = QuoteStream(_StubForStream(f"ws://127.0.0.1:{holder['port']}"))
    try:
        assert stream.start(["005930"], timeout=10)
        tick = stream.ticks.get(timeout=10)
        assert (tick["ticker"], tick["price"], tick["high"]) == ("005930", 71000.0, 71010.0)
        assert received[0]["header"]["approval_key"] == "TEST-APPROVAL"
        assert received[0]["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "005930"}
        stream.resubscribe(["000660"])
        assert stream.ticks.get(timeout=10)["ticker"] == "000660"
        assert [message["header"]["tr_type"] for message in received] == ["1", "1", "0"]
    finally:
        stream.stop()
        loop.call_soon_threadsafe(holder["stop"].set_result, None)
        thread.join(timeout=5)


def test_quote_stream_reports_failure_when_the_endpoint_is_dead():
    from mscr.broker.stream import QuoteStream

    states: list[str] = []
    stream = QuoteStream(_StubForStream("ws://127.0.0.1:9"), on_state=lambda state, _error: states.append(state))
    try:
        assert stream.start(["005930"], timeout=5) is False
        assert "reconnecting" in states
    finally:
        stream.stop()
