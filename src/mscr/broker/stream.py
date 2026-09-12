"""KIS 실시간체결가(H0STCNT0) 웹소켓 구독.

틱을 큐로만 넘기고 판단은 하지 않는다. 소비자(live.py)가 단일 스레드에서 큐를 비우므로
주문 판정·집행은 전부 한 스레드 안에서 일어나고, 여기서는 연결·재연결·구독만 책임진다.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from datetime import datetime
from typing import Any, Callable, Iterable

import websockets

TR_ID = "H0STCNT0"
MAX_SUBSCRIPTIONS = 40  # KIS가 세션당 허용하는 실시간 등록 건수.
RECONNECT_BACKOFF = (1, 2, 5, 10, 30)
# 체결가 응답은 '^'로 구분된 고정 순서 배열이다. 쓰는 값만 인덱스로 집어 온다.
FIELDS = {"ticker": 0, "time": 1, "price": 2, "open": 7, "high": 8, "low": 9, "volume": 13, "date": 33, "halted": 35}


def _number(value: str) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_frame(raw: str) -> list[dict[str, Any]]:
    """`0|H0STCNT0|002|종목^시각^현재가^…` 프레임을 틱 목록으로 푼다.

    한 프레임에 여러 틱이 이어 붙어 오므로 건수로 나눠 자른다. 필드 개수는 KIS가 늘릴 수 있어
    폭을 응답에서 역산하고, 모자란 필드는 건너뛴다."""
    parts = raw.split("|")
    if len(parts) < 4 or parts[1] != TR_ID: return []
    try:
        count = max(1, int(parts[2]))
    except ValueError:
        count = 1
    values = parts[3].split("^")
    width = len(values) // count
    if width <= FIELDS["price"]: return []
    ticks: list[dict[str, Any]] = []
    for index in range(count):
        chunk = values[index * width:(index + 1) * width]
        ticker = chunk[FIELDS["ticker"]].strip()
        price = _number(chunk[FIELDS["price"]])
        if not ticker or price <= 0: continue

        def field(key: str) -> str:
            position = FIELDS[key]
            return chunk[position] if position < len(chunk) else ""

        ticks.append({
            "ticker": ticker, "price": price,
            "open": _number(field("open")) or price, "high": _number(field("high")) or price,
            "low": _number(field("low")) or price, "volume": _number(field("volume")),
            "halted": field("halted").strip().upper() == "Y",
            "date": field("date").strip() or None, "time": field("time").strip() or None,
            "received_at": datetime.now().isoformat(timespec="seconds"),
        })
    return ticks


class QuoteStream:
    """백그라운드 스레드에서 웹소켓을 물고 틱을 큐에 넣는다. 끊기면 백오프로 다시 붙는다."""

    def __init__(self, broker, on_state: Callable[[str, str | None], None] | None = None, max_queue: int = 10000):
        self.broker = broker
        self.on_state = on_state
        self.ticks: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue)
        self.state = "stopped"
        self.last_error: str | None = None
        self._tickers: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ws: Any = None
        self._ready = threading.Event()

    @property
    def connected(self) -> bool:
        return self.state == "live"

    def start(self, tickers: Iterable[str], timeout: float = 15.0) -> bool:
        """연결이 서고 첫 구독이 나갈 때까지 기다린다. 실패하면 False — 호출자가 폴링으로 내려간다."""
        self._tickers = self._limit(tickers)
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mscr-quote-stream")
        self._thread.start()
        self._ready.wait(timeout)
        return self.connected

    def stop(self) -> None:
        self._stop.set()
        loop, ws = self._loop, self._ws
        if loop is not None and ws is not None:
            asyncio.run_coroutine_threadsafe(ws.close(), loop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._set_state("stopped")

    def resubscribe(self, tickers: Iterable[str]) -> None:
        """감시 종목이 바뀌면 차집합만 등록·해제한다."""
        wanted = self._limit(tickers)
        added, removed = [t for t in wanted if t not in self._tickers], [t for t in self._tickers if t not in wanted]
        self._tickers = wanted
        loop, ws = self._loop, self._ws
        if loop is None or ws is None or not self.connected: return
        for ticker in added:
            asyncio.run_coroutine_threadsafe(self._send(ws, ticker, "1"), loop)
        for ticker in removed:
            asyncio.run_coroutine_threadsafe(self._send(ws, ticker, "0"), loop)

    def _limit(self, tickers: Iterable[str]) -> list[str]:
        unique = list(dict.fromkeys(str(ticker).strip() for ticker in tickers if str(ticker).strip()))
        return unique[:MAX_SUBSCRIPTIONS]

    def _set_state(self, state: str, error: str | None = None) -> None:
        self.state, self.last_error = state, error or self.last_error
        if self.on_state is not None: self.on_state(state, error)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        finally:
            self._loop.close()
            self._loop = None
            self._ready.set()

    async def _serve(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self._set_state("connecting")
                key = self.broker.approval_key()
                # KIS는 앱 레벨 PINGPONG을 따로 보내므로 라이브러리 ping은 끈다(중복 하트비트로 끊기는 것을 막는다).
                async with websockets.connect(self.broker.ws_url, ping_interval=None, max_size=None) as ws:
                    self._ws = ws
                    for ticker in list(self._tickers):
                        await self._send(ws, ticker, "1", key)
                    attempt = 0
                    self._set_state("live")
                    self._ready.set()
                    await self._consume(ws)
            except Exception as exc:
                if self._stop.is_set(): break
                self._set_state("reconnecting", f"{type(exc).__name__}: {exc}")
                self._ready.set()
                await asyncio.sleep(RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)])
                attempt += 1
            finally:
                self._ws = None
        self._set_state("stopped")

    async def _consume(self, ws: Any) -> None:
        async for raw in ws:
            if self._stop.is_set(): break
            message = raw if isinstance(raw, str) else raw.decode("utf-8", "ignore")
            if message[:1] in ("0", "1"):
                for tick in parse_frame(message):
                    try:
                        self.ticks.put_nowait(tick)
                    except queue.Full:  # 소비가 밀리면 가장 오래된 틱을 버린다 — 판정에는 최신가만 쓴다.
                        self.ticks.get_nowait()
                        self.ticks.put_nowait(tick)
                continue
            try:
                system = json.loads(message)
            except ValueError:
                continue
            if system.get("header", {}).get("tr_id") == "PINGPONG":
                await ws.pong(raw)
                continue
            body = system.get("body") or {}
            if body.get("rt_cd") not in (None, "0"):
                self._set_state(self.state, f"구독 거부: {body.get('msg1')}")

    async def _send(self, ws: Any, ticker: str, tr_type: str, key: str | None = None) -> None:
        message = {
            "header": {"approval_key": key or self.broker.approval_key(), "custtype": "P", "tr_type": tr_type, "content-type": "utf-8"},
            "body": {"input": {"tr_id": TR_ID, "tr_key": ticker}},
        }
        await ws.send(json.dumps(message))
