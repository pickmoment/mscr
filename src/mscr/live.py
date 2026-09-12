"""장중 실시간 실행 데몬.

웹소켓 틱으로 계획을 감시하다 조건을 충족하면 그 자리에서 주문을 낸다. 판정·집행은 전부 이
스레드 하나에서만 일어나고, 웹소켓 스레드는 틱을 큐에 넣기만 한다.

살아 있다는 사실 자체가 안전장치의 일부라서 상태를 매 사이클 DB(`daemon_state`)에 남긴다.
프로세스가 죽으면 하트비트가 멈추고, 화면과 `mscr trade daemon-status`가 그 공백을 즉시 드러낸다.
다시 뜰 때는 REST로 현재가를 받아 **그 사이 스쳐 간 트리거를 먼저 집행**하고 나서 스트림에 붙는다.
"""

from __future__ import annotations

import queue
import threading
import time as clock
from datetime import datetime, time
from typing import Any, Callable

from . import guard
from .config import request_delay
from .db import db_session
from .trading import cancel_all_open_orders, evaluate_plans, list_plans, manage_open_orders, run_plans, sync_orders

MARKET_OPEN = time(9, 0)
ENTRY_CUTOFF = time(15, 15)   # 이후로는 신규 진입을 내지 않는다 — 종가 직전 추격 진입을 막는다.
MARKET_CLOSE = time(15, 30)
HEARTBEAT_STALE_SEC = 30
OPEN_PHASES = ("open", "closing")


def market_phase(now: datetime | None = None) -> str:
    """장 상태. 공휴일은 따로 판정하지 않는다 — 휴장일에는 틱이 오지 않아 데몬이 조용히 대기한다."""
    now = now or datetime.now()
    if now.weekday() >= 5: return "holiday"
    current = now.time()
    if current < MARKET_OPEN: return "before"
    if current >= MARKET_CLOSE: return "after"
    return "closing" if current >= ENTRY_CUTOFF else "open"


def write_state(path=None, **fields: Any) -> dict[str, Any]:
    fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
    columns = ",".join(f"{key}=?" for key in fields)
    with db_session(path) as db:
        db.execute("INSERT OR IGNORE INTO daemon_state(id,status,updated_at) VALUES(1,'stopped',?)", (fields["updated_at"],))
        db.execute(f"UPDATE daemon_state SET {columns} WHERE id=1", tuple(fields.values()))
        return dict(db.execute("SELECT * FROM daemon_state WHERE id=1").fetchone())


def daemon_status(path=None) -> dict[str, Any]:
    """데몬 상태 + 하트비트 신선도. running으로 기록돼 있는데 하트비트가 끊겼으면 프로세스가 죽은 것이다."""
    with db_session(path) as db:
        row = db.execute("SELECT * FROM daemon_state WHERE id=1").fetchone()
    if row is None:
        return {"status": "stopped", "alive": False, "stale": False, "heartbeat_age_sec": None, "phase": market_phase()}
    state = dict(row)
    age = None
    if state.get("heartbeat_at"):
        try:
            age = (datetime.now() - datetime.fromisoformat(str(state["heartbeat_at"]))).total_seconds()
        except ValueError:
            age = None
    stale = bool(state["status"] == "running" and (age is None or age > HEARTBEAT_STALE_SEC))
    return {**state, "heartbeat_age_sec": age, "stale": stale, "alive": state["status"] == "running" and not stale, "phase": market_phase()}


class Daemon:
    """계획 감시·집행 루프 한 벌. `run()`은 장이 닫히거나 stop_event가 서면 돌아온다."""

    def __init__(self, broker, dry_run: bool = True, plan_ids: list[int] | None = None, path=None, *,
                 use_stream: bool = True, poll_sec: float = 2.0, evaluate_interval: float = 0.5,
                 sync_interval: float = 5.0, manage_interval: float = 30.0, refresh_interval: float = 60.0,
                 heartbeat_interval: float = 5.0, unfilled_timeout: float = 60.0,
                 stop_event: threading.Event | None = None, on_event: Callable[[str, str, dict[str, Any]], None] | None = None,
                 wait_for_open: bool = True, max_cycles: int | None = None, now: Callable[[], datetime] = datetime.now):
        if broker is None: raise ValueError("브로커가 설정되지 않았습니다 — 실시간 감시에는 시세 연결이 필요합니다")
        self.broker, self.dry_run, self.path = broker, dry_run, path
        self.plan_ids = [int(value) for value in plan_ids] if plan_ids else None
        self.env = str(getattr(broker, "env", "") or "")
        self.use_stream, self.poll_sec = use_stream, poll_sec
        self.evaluate_interval, self.sync_interval = evaluate_interval, sync_interval
        self.manage_interval, self.refresh_interval = manage_interval, refresh_interval
        self.heartbeat_interval, self.unfilled_timeout = heartbeat_interval, unfilled_timeout
        self.stop_event = stop_event or threading.Event()
        self.on_event = on_event
        self.wait_for_open, self.max_cycles, self.now = wait_for_open, max_cycles, now
        self.quotes: dict[str, dict[str, Any]] = {}
        self.tickers: list[str] = []
        self.targets: list[int] = []
        self.stream: Any = None
        self.mode = "polling"
        self.ticks = self.orders = self.cycles = 0
        self.last_tick_at: str | None = None
        self.summary: list[dict[str, Any]] = []

    # -- 이벤트/상태 -------------------------------------------------------
    def _emit(self, kind: str, message: str, data: dict[str, Any] | None = None) -> None:
        if self.on_event is not None: self.on_event(kind, message, data or {})

    def _heartbeat(self, **extra: Any) -> None:
        write_state(self.path, heartbeat_at=self.now().isoformat(timespec="seconds"), ticks=self.ticks,
                    orders=self.orders, plans=len(self.targets), tickers=len(self.tickers),
                    stream=self.mode, last_tick_at=self.last_tick_at, **extra)

    # -- 감시 대상 ---------------------------------------------------------
    def refresh_targets(self) -> None:
        """아직 끝나지 않은 계획과 그 종목. 청산된 계획은 구독에서 빠진다."""
        evaluations = evaluate_plans(self.plan_ids, self.path, env=self.env or None)
        enabled = {plan["id"] for plan in list_plans(self.path) if plan["enabled"]}
        live = [item for item in evaluations if item["plan_id"] in enabled and item["phase"] != "closed"]
        self.targets = [item["plan_id"] for item in live]
        tickers = list(dict.fromkeys(item["ticker"] for item in live))
        if tickers != self.tickers:
            self.tickers = tickers
            if self.stream is not None and self.stream.connected: self.stream.resubscribe(tickers)

    # -- 시세 --------------------------------------------------------------
    def poll_quotes(self) -> None:
        """REST 현재가로 시세를 채운다. 시작 직후 복구와 웹소켓이 끊겼을 때의 폴백 경로다."""
        delay = request_delay()
        for ticker in self.tickers:
            if self.stop_event.is_set(): return
            try:
                quote = self.broker.current_price(ticker)
            except Exception as exc:
                self._emit("error", f"{ticker} 현재가 조회 실패: {exc}", {"ticker": ticker})
                continue
            self.quotes[ticker] = {**quote, "received_at": self.now().isoformat(timespec="seconds")}
            self.last_tick_at = self.quotes[ticker]["received_at"]
            if delay: clock.sleep(delay)

    def drain_ticks(self, limit: int = 2000) -> int:
        """큐에 쌓인 틱을 비우고 종목별 최신값만 남긴다. 판정에 필요한 건 마지막 값뿐이다."""
        drained = 0
        while drained < limit:
            try:
                tick = self.stream.ticks.get_nowait()
            except (queue.Empty, AttributeError):
                break
            self.quotes[tick["ticker"]] = tick
            self.last_tick_at = tick["received_at"]
            drained += 1
        self.ticks += drained
        return drained

    def start_stream(self) -> None:
        if not self.use_stream or not self.tickers or self.stream is not None: return
        from .broker.stream import QuoteStream

        def on_state(state: str, error: str | None) -> None:
            self.mode = "websocket" if state == "live" else "polling"
            if error: self._emit("stream", f"실시간 시세 {state} — {error}", {"state": state})

        self.stream = QuoteStream(self.broker, on_state=on_state)
        if self.stream.start(self.tickers):
            self.mode = "websocket"
            self._emit("stream", f"실시간 시세 연결 — {len(self.tickers)}종목 구독", {"tickers": self.tickers})
        else:
            self.mode = "polling"
            self._emit("stream", "실시간 시세 연결 실패 — REST 폴링으로 감시합니다", {})

    # -- 집행 --------------------------------------------------------------
    def execute(self, allow_entry: bool = True) -> list[dict[str, Any]]:
        """트리거된 계획만 골라 주문한다. 평가는 가볍고 집행은 무거우므로 두 단계로 나눈다."""
        if not self.targets or not self.quotes: return []
        evaluations = evaluate_plans(self.targets, self.path, env=self.env or None, quotes=self.quotes)
        hot = [item["plan_id"] for item in evaluations if item["triggered"] and (allow_entry or item["next_leg"] != "entry")]
        if not hot: return []
        orders = run_plans(broker=self.broker, dry_run=self.dry_run, plan_ids=hot, path=self.path, quotes=self.quotes, origin="daemon")
        for order in orders:
            self.orders += 1
            self.summary.append(order)
            self._emit("order", f"[{order['status']}] {order['plan_name']} {order['ticker']} {order['leg']} {order['side']} {order['quantity']:g}주 — {order.get('message') or ''}".rstrip(), order)
        return orders

    def sync(self) -> None:
        if self.dry_run: return
        try:
            updated = sync_orders(broker=self.broker, path=self.path)
        except Exception as exc:
            self._emit("error", f"체결 동기화 실패: {exc}", {})
            return
        for order in updated:
            if order["status"] == "filled":
                self._emit("fill", f"체결 {order['ticker']} {order['leg']} {order['filled_quantity']:g}주 @{order['filled_price'] or 0:g}", order)

    def manage(self, cancel_entries: bool = False) -> None:
        if self.dry_run: return
        try:
            handled = manage_open_orders(broker=self.broker, path=self.path, timeout_sec=self.unfilled_timeout, cancel_entries=cancel_entries)
        except Exception as exc:
            self._emit("error", f"미체결 정리 실패: {exc}", {})
            return
        for order in handled:
            self._emit("manage", f"미체결 정리 {order['ticker']} {order['leg']} → {order['status']} — {order.get('message') or ''}".rstrip(), order)

    # -- 루프 --------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        mode = "live" if not self.dry_run else "dry-run"
        write_state(self.path, status="running", env=self.env, pid=_pid(), mode=mode, stream="starting",
                    started_at=self.now().isoformat(timespec="seconds"), stopped_at=None, last_error=None,
                    message=None, ticks=0, orders=0, heartbeat_at=self.now().isoformat(timespec="seconds"))
        self._emit("start", f"데몬 시작 — env={self.env or '-'} mode={mode}", {"mode": mode})
        reason = "stopped"
        try:
            reason = self._loop()
        except KeyboardInterrupt:
            reason = "interrupted"
        except Exception as exc:
            reason = "failed"
            write_state(self.path, last_error=f"{type(exc).__name__}: {exc}")
            self._emit("error", f"데몬 중단: {type(exc).__name__}: {exc}", {})
            raise
        finally:
            if self.stream is not None: self.stream.stop()
            write_state(self.path, status="failed" if reason == "failed" else "stopped", stream="off",
                        stopped_at=self.now().isoformat(timespec="seconds"), message=reason,
                        heartbeat_at=self.now().isoformat(timespec="seconds"))
            self._emit("stop", f"데몬 종료 ({reason}) — 틱 {self.ticks}건, 주문 {self.orders}건", {"reason": reason})
        return {"reason": reason, "ticks": self.ticks, "orders": self.orders, "cycles": self.cycles, "orders_detail": self.summary}

    def _loop(self) -> str:
        last = {"evaluate": 0.0, "sync": 0.0, "manage": 0.0, "refresh": 0.0, "heartbeat": 0.0, "poll": 0.0}
        recovered = closing_done = False
        while not self.stop_event.is_set():
            if self.max_cycles is not None and self.cycles >= self.max_cycles: return "max_cycles"
            self.cycles += 1
            phase = market_phase(self.now())
            if phase in ("before", "holiday"):
                if not self.wait_for_open: return phase
                self._heartbeat(status="running", message=f"장 대기 중 ({phase})")
                if self.stop_event.wait(min(30.0, self.poll_sec * 10)): return "stopped"
                continue
            if phase == "after":
                self.manage(cancel_entries=True)
                self.sync()
                return "market_closed"

            now = clock.monotonic()
            if now - last["refresh"] >= self.refresh_interval or not self.tickers:
                self.refresh_targets()
                last["refresh"] = now
            if not self.targets:
                self._heartbeat(status="running", message="감시할 계획이 없습니다")
                if self.stop_event.wait(min(15.0, self.refresh_interval)): return "stopped"
                continue

            self.start_stream()
            if not recovered:
                # 재시작·첫 기동 복구: 스트림 이전에 REST로 현재 상태를 확인해 그 사이 지나간 트리거를 먼저 집행한다.
                self.poll_quotes()
                self.execute(allow_entry=phase == "open")
                recovered = True
                last["evaluate"] = clock.monotonic()

            if self.stream is not None and self.stream.connected:
                self.drain_ticks()
            elif now - last["poll"] >= self.poll_sec:
                self.poll_quotes()
                last["poll"] = now

            now = clock.monotonic()
            if now - last["evaluate"] >= self.evaluate_interval:
                self.execute(allow_entry=phase == "open")
                last["evaluate"] = now
            if now - last["sync"] >= self.sync_interval:
                self.sync()
                last["sync"] = now
            if now - last["manage"] >= self.manage_interval:
                self.manage()
                last["manage"] = now
            if phase == "closing" and not closing_done:
                # 장 마감 직전에는 붙지 않은 진입을 거둬들인다 — 종가에 원치 않는 체결이 남지 않도록.
                self.manage(cancel_entries=True)
                closing_done = True
            if now - last["heartbeat"] >= self.heartbeat_interval:
                self._heartbeat(status="running", message=None)
                last["heartbeat"] = now
            if self.stop_event.wait(0.05 if self.mode == "websocket" else min(0.5, self.poll_sec)): return "stopped"
        return "stopped"


def _pid() -> int:
    import os
    return os.getpid()


def run_daemon(broker=None, dry_run: bool = True, plan_ids: list[int] | None = None, path=None, **options: Any) -> dict[str, Any]:
    if not dry_run:
        current = guard.settings(path)
        if current["trade_kill_switch"]:
            raise ValueError(f"킬스위치가 올라가 있습니다 — {current['trade_kill_reason'] or '사유 미기록'} (mscr trade guard --release)")
    return Daemon(broker, dry_run=dry_run, plan_ids=plan_ids, path=path, **options).run()


def panic(broker=None, reason: str = "수동 정지", path=None) -> dict[str, Any]:
    """킬스위치를 올리고 시장에 걸려 있는 주문을 전부 거둬들인다."""
    state = guard.engage(reason, path)
    cancelled = cancel_all_open_orders(broker, path) if broker is not None else []
    write_state(path, status="halted", message=f"panic — {reason}")
    return {"guard": state, "cancelled": cancelled}
