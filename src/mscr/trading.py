from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from . import guard
from .db import db_session
from .market import bar_source
from .portfolio import snapshot

SIDES = {"buy", "sell"}
ORDER_TYPES = {"limit", "market"}
OPEN_STATUSES = ("submitted", "partial")
# 이 상태의 레그는 "이미 시도했다"로 보고 다시 주문하지 않는다. cancelled가 여기 있는 이유는
# 미체결 진입을 취소한 뒤 같은 자리에 다시 들어가지 않기 위해서다(부분체결분은 filled_quantity에 남는다).
ACTIVE_LEG_STATUSES = ("dry_run", "submitted", "partial", "filled", "cancelled")
ORDER_SELECT = "SELECT o.*, p.name plan_name FROM broker_orders o LEFT JOIN trade_plans p ON p.id=o.plan_id"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _plan(row: Any) -> dict[str, Any]:
    plan = dict(row)
    plan["enabled"] = bool(plan["enabled"])
    return plan


def _order(db, order_id: int) -> dict[str, Any]:
    row = db.execute(f"{ORDER_SELECT} WHERE o.id=?", (order_id,)).fetchone()
    return dict(row) if row else {}


def list_plans(path=None) -> list[dict[str, Any]]:
    with db_session(path) as db:
        return [_plan(row) for row in db.execute("SELECT * FROM trade_plans ORDER BY name").fetchall()]


def save_plan(plan: dict[str, Any], path=None) -> int:
    name = str(plan.get("name") or "").strip()
    if not 1 <= len(name) <= 60: raise ValueError("계획 이름은 1~60자여야 합니다")
    ticker = str(plan.get("ticker") or "").strip()
    if len(ticker) != 6 or not ticker.isdigit(): raise ValueError("종목코드는 6자리 숫자여야 합니다")
    side = str(plan.get("side") or "")
    if side not in SIDES: raise ValueError("매매 구분은 buy 또는 sell 이어야 합니다")
    order_type = str(plan.get("order_type") or "")
    if order_type not in ORDER_TYPES: raise ValueError("주문 유형은 limit 또는 market 이어야 합니다")
    try:
        quantity = float(plan.get("quantity"))
    except (TypeError, ValueError):
        raise ValueError("수량이 올바르지 않습니다") from None
    if not quantity > 0: raise ValueError("수량은 0보다 커야 합니다")
    raw_price = plan.get("limit_price")
    try:
        limit_price = None if raw_price is None or str(raw_price).strip() == "" else float(raw_price)
    except (TypeError, ValueError):
        raise ValueError("주문 가격이 올바르지 않습니다") from None
    if order_type == "market": limit_price = None
    elif limit_price is None or limit_price <= 0: raise ValueError("지정가 주문은 0보다 큰 주문 가격이 필요합니다")

    def positive(key: str, label: str) -> float:
        try:
            value = float(plan.get(key))
        except (TypeError, ValueError):
            raise ValueError(f"{label}이 올바르지 않습니다") from None
        if not value > 0: raise ValueError(f"{label}은 0보다 커야 합니다")
        return value

    def ratio(key: str, label: str) -> float:
        try:
            value = float(plan.get(key))
        except (TypeError, ValueError):
            raise ValueError(f"{label}이 올바르지 않습니다") from None
        if not 0 < value < 1: raise ValueError(f"{label}은 0~1 사이여야 합니다 (예: 30% → 0.3)")
        return value

    entry_price = positive("entry_price", "진입가")
    stop_price = positive("stop_price", "손절가")
    tp1_price = positive("tp1_price", "1차 익절가")
    tp1_ratio = ratio("tp1_ratio", "1차 익절 비율")
    tp2_price = positive("tp2_price", "2차 익절가")
    tp2_ratio = ratio("tp2_ratio", "2차 익절 비율")
    tp3_trailing_pct = positive("tp3_trailing_pct", "트레일링 스탑 비율")
    if tp1_ratio + tp2_ratio >= 1: raise ValueError("1차+2차 익절 비율의 합은 1보다 작아야 합니다 (나머지는 3차 트레일링 스탑 대상입니다)")
    if side == "buy":
        if not stop_price < entry_price: raise ValueError("매수 계획은 손절가가 진입가보다 낮아야 합니다")
        if not entry_price < tp1_price < tp2_price: raise ValueError("매수 계획은 진입가 < 1차 익절가 < 2차 익절가 순서여야 합니다")
    else:
        if not stop_price > entry_price: raise ValueError("매도 계획은 손절가가 진입가보다 높아야 합니다")
        if not entry_price > tp1_price > tp2_price: raise ValueError("매도 계획은 진입가 > 1차 익절가 > 2차 익절가 순서여야 합니다")

    enabled = int(bool(plan.get("enabled", True)))
    note = plan.get("note") or None
    setup = (str(plan.get("setup")).strip() or None) if plan.get("setup") is not None else None
    plan_id = int(plan["id"]) if plan.get("id") else None
    now = _now()
    values = (name, ticker, side, quantity, order_type, limit_price, entry_price, stop_price, tp1_price, tp1_ratio, tp2_price, tp2_ratio, tp3_trailing_pct, enabled, setup, note)
    with db_session(path) as db:
        if not db.execute("SELECT 1 FROM instruments WHERE ticker=?", (ticker,)).fetchone(): raise ValueError("등록되지 않은 종목코드입니다")
        clash = db.execute("SELECT id FROM trade_plans WHERE name=?", (name,)).fetchone()
        if clash and clash[0] != plan_id: raise ValueError("같은 이름의 계획이 있습니다")
        if plan_id is None:
            return int(db.execute(
                "INSERT INTO trade_plans(name,ticker,side,quantity,order_type,limit_price,entry_price,stop_price,tp1_price,tp1_ratio,tp2_price,tp2_ratio,tp3_trailing_pct,enabled,setup,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values + (now, now)).lastrowid)
        if not db.execute(
            "UPDATE trade_plans SET name=?,ticker=?,side=?,quantity=?,order_type=?,limit_price=?,entry_price=?,stop_price=?,tp1_price=?,tp1_ratio=?,tp2_price=?,tp2_ratio=?,tp3_trailing_pct=?,enabled=?,setup=?,note=?,updated_at=? WHERE id=?",
            values + (now, plan_id)).rowcount: raise ValueError("계획을 찾을 수 없습니다")
        return plan_id


def delete_plan(plan_id: int, path=None) -> None:
    with db_session(path) as db:
        db.execute("DELETE FROM trade_plans WHERE id=?", (int(plan_id),))


def _plan_legs(db, plan_ids: list[int], env: str | None = None) -> dict[int, dict[str, dict[str, Any]]]:
    """Latest broker_orders row per (plan_id, leg), keyed by plan_id then leg.

    env를 주면 그 환경의 주문만 본다. 같은 계획을 모의계좌에서 먼저 돌려 검증하더라도 실계좌
    진행 단계가 모의 체결 때문에 앞서 나가지 않는다."""
    if not plan_ids: return {}
    query = f"SELECT * FROM broker_orders WHERE plan_id IN ({','.join('?' * len(plan_ids))}) AND leg IS NOT NULL"
    params = list(plan_ids)
    if env:
        query += " AND env=?"
        params.append(env)
    rows = db.execute(f"{query} ORDER BY id", params).fetchall()
    result: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        order = dict(row)
        result.setdefault(order["plan_id"], {})[order["leg"]] = order
    return result


def _latest_bar(db, ticker: str) -> dict[str, Any] | None:
    row = db.execute(f"SELECT date,open,high,low,close,volume,halted FROM daily_bars WHERE ticker=? AND source='{bar_source()}' ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
    return dict(row) if row else None


def _trailing_reference(db, ticker: str, side: str, since_date: str) -> float | None:
    column = "MAX(high)" if side == "buy" else "MIN(low)"
    value = db.execute(f"SELECT {column} FROM daily_bars WHERE ticker=? AND source='{bar_source()}' AND date>=?", (ticker, since_date)).fetchone()[0]
    return float(value) if value is not None else None


def _leg_quantities(quantity: float, tp1_ratio: float, tp2_ratio: float) -> tuple[float, float, float]:
    """tp1/tp2/트레일링 레그 수량. KRX에는 소수 주식이 없으므로 계획 수량이 정수면 정수로 배분하고,
    나머지 레그(트레일링)가 반올림 오차를 흡수해 세 레그의 합이 항상 계획 수량과 정확히 일치한다.
    수량 컬럼이 REAL이라 사용자가 소수 수량을 저장했을 수 있는데, 그 경우에만 기존 비율 그대로의
    float 배분을 유지한다(임의로 정수로 깎지 않는다)."""
    if not float(quantity).is_integer():
        return quantity * tp1_ratio, quantity * tp2_ratio, quantity * (1 - tp1_ratio - tp2_ratio)
    total = int(quantity)
    first, second = round(total * tp1_ratio), round(total * tp2_ratio)
    # 비율은 0<r<1 이고 tp1+tp2<1 로 검증되므로 first+second <= total, 즉 나머지는 음수가 되지 않는다.
    return float(first), float(second), float(total - first - second)


def _leg_filled(info: dict[str, Any] | None, planned: float) -> float:
    """레그가 실제로 시장에서 채운 수량. 체결 정보가 아직 없으면 주문 수량을 그대로 본다
    (접수 직후·모의 실행처럼 체결 조회 전 단계)."""
    if not info: return 0.0
    filled = float(info.get("filled_quantity") or 0)
    if filled > 0: return filled
    return planned if info["status"] in ACTIVE_LEG_STATUSES else 0.0


def _position(plan: dict[str, Any], legs: dict[str, dict[str, Any]]) -> dict[str, float]:
    """계획이 실제로 들고 있는 수량과 각 청산 레그의 주문 수량.

    분모는 계획 수량이 아니라 **진입 실체결 수량**이다. 지정가 진입이 부분체결로 끝나면 익절
    배분도 그 수량에서 다시 나눠야 보유보다 많이 파는 주문이 나가지 않는다."""
    quantity = float(plan["quantity"])
    entry = _leg_filled(legs.get("entry"), quantity)
    tp1_qty, tp2_qty, trailing_qty = _leg_quantities(entry, plan["tp1_ratio"], plan["tp2_ratio"]) if entry > 0 else (0.0, 0.0, 0.0)
    remaining = entry - _leg_filled(legs.get("tp1"), tp1_qty) - _leg_filled(legs.get("tp2"), tp2_qty)
    return {"entry": entry, "tp1": tp1_qty, "tp2": tp2_qty, "trailing": trailing_qty, "remaining": max(0.0, remaining)}


def _plan_evaluation(db, plan: dict[str, Any], legs: dict[str, dict[str, Any]], latest_date: str | None, quote: dict[str, Any] | None = None) -> dict[str, Any]:
    ticker = plan["ticker"]
    bar = _latest_bar(db, ticker)
    live = quote is not None
    if not live and bar is None:
        return {"phase": "waiting_entry", "next_leg": None, "triggered": False, "reason": "일봉 데이터 없음", "as_of": None, "close": None, "order_side": None, "order_quantity": None, "live": False, "halted": False}
    long = plan["side"] == "buy"
    exit_side = "sell" if long else "buy"
    close = float(quote["price"]) if live else float(bar["close"])
    as_of = datetime.now().strftime("%Y-%m-%d") if live else bar["date"]
    halted = bool(quote.get("halted")) if live else bool(bar.get("halted"))
    stale = not live and latest_date is not None and bar["date"] != latest_date
    session_high = float(quote["high"]) if live else float(bar["high"])
    session_low = float(quote["low"]) if live else float(bar["low"])
    # 진입·익절은 **지금 가격**으로 본다 — 이미 스쳐 지나간 가격을 뒤늦게 추격해 봐야 계획과 다른 자리에
    # 들어가고 나올 뿐이다. 반대로 손절·트레일링은 **당일 고저**로 본다 — 데몬이 끊긴 사이 스쳐 간
    # 이탈도 반드시 잡아야 하기 때문이다. 일봉 모드에서는 둘 다 그 봉의 고저가 된다.
    reach_up, reach_down = (close, close) if live else (session_high, session_low)

    def hit_up(level: float) -> bool: return reach_up >= level        # 진입·익절: 지금 가격
    def hit_down(level: float) -> bool: return reach_down <= level
    def breached(level: float) -> bool:                                # 손절·트레일링: 당일 고저
        return session_low <= level if long else session_high >= level

    base = {"as_of": as_of, "close": close, "live": live, "halted": halted}
    done = {leg: info["status"] in ACTIVE_LEG_STATUSES for leg, info in legs.items()}
    entry_done, stop_done = done.get("entry", False), done.get("stop", False)
    tp1_done, tp2_done, trailing_done = done.get("tp1", False), done.get("tp2", False), done.get("trailing", False)

    if stop_done or trailing_done:
        return {**base, "phase": "closed", "next_leg": None, "triggered": False, "reason": "청산 완료", "order_side": None, "order_quantity": None}

    if not entry_done:
        hit = hit_up(plan["entry_price"]) if long else hit_down(plan["entry_price"])
        reason = "일봉이 오래되었습니다" if stale else "거래정지 종목입니다" if halted else ("진입가 돌파 → 진입 주문 대상" if hit else f"진입가 대기 중 (진입가 {plan['entry_price']:g}, 현재가 {close:g})")
        return {**base, "phase": "waiting_entry", "next_leg": "entry", "triggered": hit, "reason": reason, "order_side": plan["side"], "order_quantity": float(plan["quantity"])}

    position = _position(plan, legs)
    tp1_qty, tp2_qty, remaining_qty = position["tp1"], position["tp2"], position["remaining"]
    tp1_pending, tp2_pending = not tp1_done and tp1_qty > 0, not tp2_done and tp2_qty > 0
    if remaining_qty <= 0:
        reason = "진입 미체결 — 청산 대상 없음" if position["entry"] <= 0 else "청산 완료 — 남은 수량 없음"
        return {**base, "phase": "closed", "next_leg": None, "triggered": False, "reason": reason, "order_side": None, "order_quantity": None}
    phase = "holding" if tp1_pending else "tp1_done" if tp2_pending else "trailing"

    if breached(plan["stop_price"]):
        reason = "일봉이 오래되었습니다" if stale else "손절가 이탈 → 즉시 청산"
        return {**base, "phase": phase, "next_leg": "stop", "triggered": True, "reason": reason, "order_side": exit_side, "order_quantity": remaining_qty}

    if tp1_pending:
        hit = hit_up(plan["tp1_price"]) if long else hit_down(plan["tp1_price"])
        reason = "일봉이 오래되었습니다" if stale else ("1차 목표가 도달 → 1차 익절" if hit else f"1차 목표가 대기 중 ({plan['tp1_price']:g})")
        return {**base, "phase": "holding", "next_leg": "tp1", "triggered": hit, "reason": reason, "order_side": exit_side, "order_quantity": min(tp1_qty, remaining_qty)}

    if tp2_pending:
        hit = hit_up(plan["tp2_price"]) if long else hit_down(plan["tp2_price"])
        reason = "일봉이 오래되었습니다" if stale else ("2차 목표가 도달 → 2차 익절" if hit else f"2차 목표가 대기 중 ({plan['tp2_price']:g})")
        return {**base, "phase": "tp1_done", "next_leg": "tp2", "triggered": hit, "reason": reason, "order_side": exit_side, "order_quantity": min(tp2_qty, remaining_qty)}

    since_date = str((legs["tp2"] if tp2_done else legs["tp1"] if tp1_done else legs["entry"])["as_of"])
    reference = _trailing_reference(db, ticker, plan["side"], since_date)
    if live:  # 장중 신고가는 아직 일봉에 없다 — 실시간 세션 극값을 함께 본다.
        reference = session_high if reference is None else max(reference, session_high)
        if not long: reference = session_low if reference is None else min(reference, session_low)
    if reference is None:
        return {**base, "phase": "trailing", "next_leg": "trailing", "triggered": False, "reason": "트레일링 기준 데이터 없음", "order_side": exit_side, "order_quantity": remaining_qty}
    trail_level = reference * (1 - plan["tp3_trailing_pct"] / 100) if long else reference * (1 + plan["tp3_trailing_pct"] / 100)
    hit = breached(trail_level)
    reason = "일봉이 오래되었습니다" if stale else (f"트레일링 스탑 이탈({trail_level:g}) → 전량 청산" if hit else f"트레일링 스탑 감시 중 (기준 {reference:g}, 이탈가 {trail_level:g})")
    return {**base, "phase": "trailing", "next_leg": "trailing", "triggered": hit, "reason": reason, "order_side": exit_side, "order_quantity": remaining_qty}


def evaluate_plans(plan_ids: list[int] | None = None, path=None, env: str | None = None, quotes: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """계획별 현재 단계와 다음 동작. quotes에 종목별 실시간 시세를 주면 일봉 대신 그 값으로 판정한다."""
    wanted = {int(value) for value in plan_ids} if plan_ids else None
    with db_session(path) as db:
        plans = [_plan(row) for row in db.execute("SELECT * FROM trade_plans ORDER BY name").fetchall() if wanted is None or row["id"] in wanted]
        latest_date = db.execute(f"SELECT MAX(date) FROM daily_bars WHERE source='{bar_source()}'").fetchone()[0]
        legs_by_plan = _plan_legs(db, [plan["id"] for plan in plans], env)
        output = []
        for plan in plans:
            evaluation = _plan_evaluation(db, plan, legs_by_plan.get(plan["id"], {}), latest_date, (quotes or {}).get(plan["ticker"]))
            if evaluation["triggered"] and not plan["enabled"]:
                evaluation = {**evaluation, "triggered": False, "reason": "비활성 계획"}
            output.append({"plan_id": plan["id"], "name": plan["name"], "ticker": plan["ticker"], "side": plan["side"], "setup": plan["setup"], **evaluation})
        return output


def plan_exposure(path=None) -> list[dict[str, Any]]:
    """계획별 체결 진행 상태와 아직 시장에 남아 있는 수량. 리스크 집계(risk)와 결산(review)이
    단계 판정을 각자 다시 구현하지 않도록 한 곳에서 계산한다. 모의 실행은 주문을 저장하지 않으므로
    여기 들어오는 레그는 모두 실제 시도다."""
    output: list[dict[str, Any]] = []
    with db_session(path) as db:
        plans = [_plan(row) for row in db.execute("SELECT * FROM trade_plans ORDER BY name").fetchall()]
        legs_by_plan = _plan_legs(db, [plan["id"] for plan in plans])
        for plan in plans:
            legs = legs_by_plan.get(plan["id"], {})
            done = {leg: info["status"] in ACTIVE_LEG_STATUSES for leg, info in legs.items()}
            position = _position(plan, legs)
            entered = done.get("entry", False)
            closed = done.get("stop", False) or done.get("trailing", False)
            remaining = 0.0 if closed or not entered else position["remaining"]
            phase = "closed" if closed or (entered and remaining <= 0) else "waiting_entry" if not entered else "trailing" if done.get("tp2") else "tp1_done" if done.get("tp1") else "holding"
            bar = _latest_bar(db, plan["ticker"])
            entry_leg = legs.get("entry", {})
            output.append({
                **plan, "plan_id": plan["id"], "phase": phase, "entered": entered, "closed": phase == "closed",
                "remaining_quantity": max(0.0, remaining), "close": float(bar["close"]) if bar else None, "as_of": bar["date"] if bar else None,
                "entry_fill_price": entry_leg.get("filled_price"), "entry_date": entry_leg.get("as_of"),
                "legs": {leg: {"status": info["status"], "as_of": info["as_of"], "filled_quantity": info["filled_quantity"], "filled_price": info["filled_price"], "fee": info["fee"], "tax": info["tax"], "requested_at": info["requested_at"]} for leg, info in legs.items()},
            })
    return output


def run_plans(broker=None, dry_run: bool = True, plan_ids: list[int] | None = None, path=None,
              quotes: dict[str, dict[str, Any]] | None = None, origin: str = "manual") -> list[dict[str, Any]]:
    """조건을 충족한 계획을 한 레그씩 집행한다. quotes를 주면 실시간 시세로 판정한다."""
    if not dry_run and broker is None: raise ValueError("브로커가 설정되지 않았습니다")
    env = str(getattr(broker, "env", "") or "")
    evaluations = {item["plan_id"]: item for item in evaluate_plans(plan_ids, path, env=env or None, quotes=quotes)}
    plans = {plan["id"]: plan for plan in list_plans(path)}
    holdings = {row["ticker"]: float(row["quantity"]) for row in snapshot(path)["positions"]}
    limits, used, verified = guard.settings(path), guard.usage(env or None, path), guard.verified_plans(path)
    now = _now()
    recorded: list[dict[str, Any]] = []
    with db_session(path) as db:
        latest = db.execute(f"SELECT MAX(date) FROM daily_bars WHERE source='{bar_source()}'").fetchone()[0]
        reserved = {row[0]: float(row[1] or 0) for row in db.execute(f"SELECT ticker, SUM(quantity - filled_quantity) FROM broker_orders WHERE side='sell' AND status IN ({','.join('?' * len(OPEN_STATUSES))}) GROUP BY ticker", OPEN_STATUSES).fetchall()}
        for plan_id, evaluation in evaluations.items():
            plan = plans.get(plan_id)
            if plan is None or not plan["enabled"] or not evaluation["triggered"] or evaluation["next_leg"] is None: continue
            leg = evaluation["next_leg"]
            order_side = evaluation["order_side"]
            quantity = float(evaluation["order_quantity"])
            order_type = plan["order_type"] if leg == "entry" else "market"
            limit_price = plan["limit_price"] if leg == "entry" else None
            trigger_price = evaluation.get("close")
            notional = quantity * float(limit_price or trigger_price or 0)
            allowed, blocked_reason = guard.check(leg, env or "paper", plan_id, notional, path, limits, used, verified)
            order = {"status": "dry_run" if dry_run else "submitted", "broker_order_id": None, "org_no": None, "message": "모의 실행 — 저장되지 않는 미리보기입니다" if dry_run else "", "payload": None}
            available = holdings.get(plan["ticker"], 0.0) - reserved.get(plan["ticker"], 0.0)
            shortfall = leg != "entry" and order_side == "sell" and quantity > available + 1e-9
            if not allowed:
                order = {"status": "skipped", "broker_order_id": None, "org_no": None, "message": f"안전장치 차단 — {blocked_reason}", "payload": None}
            elif shortfall and available <= 0:
                order = {"status": "skipped", "broker_order_id": None, "org_no": None, "message": f"보유 수량 없음: 주문 가능 {available:g}, 요청 {quantity:g}", "payload": None}
            elif evaluation.get("halted"):
                order = {"status": "skipped", "broker_order_id": None, "org_no": None, "message": "거래정지 종목입니다", "payload": None}
            elif not evaluation.get("live") and evaluation["as_of"] != latest:
                order = {"status": "skipped", "broker_order_id": None, "org_no": None, "message": f"일봉이 오래되었습니다: 종목 {evaluation['as_of']}, 최신 {latest}", "payload": None}
            else:
                if shortfall:
                    # 청산은 통째로 거르지 않고 실제 보유만큼이라도 내보낸다. 손절을 "보유 부족"으로
                    # 스킵하는 쪽이 훨씬 위험하다. 어긋난 수량은 reconcile로 맞춘다.
                    order["message"] = f"보유 수량에 맞춰 {quantity:g}주 → {available:g}주로 줄여 주문합니다"
                    quantity = available
                if not dry_run:
                    try:
                        result = broker.submit_order(plan["ticker"], order_side, quantity, order_type, limit_price)
                        order = {"status": result.status, "broker_order_id": result.broker_order_id, "org_no": result.org_no,
                                 "message": " · ".join(filter(None, (order["message"], result.message))), "payload": json.dumps(result.payload, ensure_ascii=False, default=str)}
                    except Exception as exc:
                        order = {"status": "failed", "broker_order_id": None, "org_no": None, "message": f"{type(exc).__name__}: {exc}", "payload": None}
            if leg == "entry" and order["status"] in ("dry_run", "submitted", "partial", "filled"):
                used = {**used, "entries": used["entries"] + 1, "notional_krw": used["notional_krw"] + notional}
            row = {"plan_id": plan["id"], "plan_name": plan["name"], "leg": leg, "as_of": evaluation["as_of"], "ticker": plan["ticker"], "side": order_side,
                   "quantity": quantity, "order_type": order_type, "limit_price": limit_price, "status": order["status"], "env": env,
                   "broker_order_id": order["broker_order_id"], "org_no": order["org_no"], "origin": origin, "trigger_price": trigger_price,
                   "message": order["message"], "payload": order["payload"]}
            if dry_run:
                recorded.append({"id": None, **row, "filled_quantity": 0.0, "filled_price": None, "fee": 0.0, "tax": 0.0, "trade_id": None, "replaces_order_id": None, "requested_at": now, "updated_at": now})
                continue
            if order_side == "sell" and order["status"] in OPEN_STATUSES: reserved[plan["ticker"]] = reserved.get(plan["ticker"], 0.0) + quantity
            recorded.append(_insert_order(db, row, now))
    return recorded


def _insert_order(db, row: dict[str, Any], now: str) -> dict[str, Any]:
    """주문 1건을 기록한다. 같은 브로커 주문번호가 이미 있으면(재전송·중복 응답) 기록만 남기고 넘어간다."""
    columns = "plan_id,leg,as_of,ticker,side,quantity,order_type,limit_price,status,env,broker_order_id,org_no,origin,trigger_price,replaces_order_id,message,payload,requested_at,updated_at"
    values = (row["plan_id"], row["leg"], row["as_of"], row["ticker"], row["side"], row["quantity"], row["order_type"], row["limit_price"],
              row["status"], row["env"], row["broker_order_id"], row["org_no"], row.get("origin", "manual"), row.get("trigger_price"),
              row.get("replaces_order_id"), row["message"], row["payload"], now, now)
    statement = f"INSERT INTO broker_orders({columns}) VALUES({','.join('?' * len(values))})"
    try:
        cursor = db.execute(statement, values)
    except sqlite3.IntegrityError:
        fallback = list(values)
        fallback[8], fallback[10] = "failed", None  # status, broker_order_id
        fallback[15] = f"중복 주문번호 {row['broker_order_id']} — 주문 기록만 남깁니다"
        cursor = db.execute(statement, fallback)
    return _order(db, int(cursor.lastrowid))


def simulate_plan(plan_id: int, start: str, end: str, path=None) -> dict[str, Any]:
    """계획을 실제로 걸지 않고 [start, end] 구간 일봉으로 진입→익절1→익절2→트레일링 전이를 재생한다.

    run_plans의 모의 실행은 오늘 하루치 저장된 상태만 보므로, 진입이 한 번도 체결로 기록된 적 없는
    계획은 청산 로직에 영원히 도달하지 못한다(entry_done이 항상 False라 _plan_evaluation이 진입
    단계에서 조기 반환한다). 여기서는 계획 파라미터로 과거 일봉을 순회하며 진입·청산 상태를 이
    함수 호출 안에서만 들고 재생하므로, 한 번도 체결한 적 없는 계획이라도 "이 기간에 걸었으면
    지금 어디까지 갔을지"를 미리 볼 수 있다. DB에는 아무것도 쓰지 않는다.

    하루에는 전이(진입/손절/1차/2차/트레일링) 하나만 반영한다 — 한 봉 안에서 여러 레그가 겹쳐
    닿아도 그 순서를 일봉만으로는 알 수 없기 때문이다. 손절이 최우선이라는 순서는 evaluate_plans와
    동일하게 지킨다.
    """
    if start > end: raise ValueError("시작일은 종료일보다 앞서야 합니다")
    with db_session(path) as db:
        row = db.execute("SELECT * FROM trade_plans WHERE id=?", (int(plan_id),)).fetchone()
        if row is None: raise ValueError("계획을 찾을 수 없습니다")
        plan = _plan(row)
        bars = [dict(r) for r in db.execute(
            f"SELECT date,high,low,close FROM daily_bars WHERE ticker=? AND source='{bar_source()}' AND date>=? AND date<=? ORDER BY date",
            (plan["ticker"], start, end)).fetchall()]

    long = plan["side"] == "buy"
    direction = 1.0 if long else -1.0
    quantity = float(plan["quantity"])
    entry_price, stop_price = float(plan["entry_price"]), float(plan["stop_price"])
    tp1_price, tp2_price, trailing_pct = float(plan["tp1_price"]), float(plan["tp2_price"]), float(plan["tp3_trailing_pct"])
    tp1_qty, tp2_qty, _trailing_qty = _leg_quantities(quantity, plan["tp1_ratio"], plan["tp2_ratio"])
    r_unit = abs(entry_price - stop_price)

    legs: list[dict[str, Any]] = []
    phase = "waiting_entry"
    tp1_done = tp2_done = False
    trailing_extreme: float | None = None

    for bar in bars:
        if phase == "waiting_entry":
            hit = bar["high"] >= entry_price if long else bar["low"] <= entry_price
            if hit:
                legs.append({"leg": "entry", "date": bar["date"], "price": entry_price, "quantity": quantity})
                phase = "holding"
            continue
        remaining = quantity - (tp1_qty if tp1_done else 0.0) - (tp2_qty if tp2_done else 0.0)
        stop_hit = bar["low"] <= stop_price if long else bar["high"] >= stop_price
        if stop_hit:
            legs.append({"leg": "stop", "date": bar["date"], "price": stop_price, "quantity": remaining})
            phase = "closed"
            break
        tp1_pending, tp2_pending = not tp1_done and tp1_qty > 0, not tp2_done and tp2_qty > 0
        if tp1_pending:
            hit = bar["high"] >= tp1_price if long else bar["low"] <= tp1_price
            if hit:
                legs.append({"leg": "tp1", "date": bar["date"], "price": tp1_price, "quantity": tp1_qty})
                tp1_done = True
                trailing_extreme = bar["high"] if long else bar["low"]
                phase = "tp1_done"
            continue
        if tp2_pending:
            hit = bar["high"] >= tp2_price if long else bar["low"] <= tp2_price
            if hit:
                legs.append({"leg": "tp2", "date": bar["date"], "price": tp2_price, "quantity": tp2_qty})
                tp2_done = True
                trailing_extreme = bar["high"] if long else bar["low"]
                phase = "trailing"
            continue
        # 트레일링 단계 — 마지막으로 채워진 레그의 날부터 극값을 계속 갱신한다(_trailing_reference와 같은 규약).
        trailing_extreme = max(trailing_extreme, bar["high"]) if long else min(trailing_extreme, bar["low"])
        trail_level = trailing_extreme * (1 - trailing_pct / 100) if long else trailing_extreme * (1 + trailing_pct / 100)
        hit = bar["low"] <= trail_level if long else bar["high"] >= trail_level
        if hit:
            legs.append({"leg": "trailing", "date": bar["date"], "price": trail_level, "quantity": remaining})
            phase = "closed"
            break
        phase = "trailing"

    entered = any(leg["leg"] == "entry" for leg in legs)
    last_close = float(bars[-1]["close"]) if bars else None
    position_r = r_unit * quantity
    realized_krw = realized_r = open_quantity = open_r = total_r = None
    if entered:
        exit_legs = [leg for leg in legs if leg["leg"] != "entry"]
        realized_krw = sum((leg["price"] - entry_price) * leg["quantity"] * direction for leg in exit_legs)
        realized_r = realized_krw / position_r if position_r else None
        exited_quantity = sum(leg["quantity"] for leg in exit_legs)
        open_quantity = 0.0 if phase == "closed" else max(0.0, quantity - exited_quantity)
        open_r = (last_close - entry_price) * open_quantity * direction / position_r if open_quantity > 0 and last_close is not None and position_r else None
        total_r = None if realized_r is None and open_r is None else (realized_r or 0.0) + (open_r or 0.0)

    warnings: list[str] = []
    if not bars: warnings.append("구간에 일봉이 없습니다")
    elif not entered: warnings.append("구간 안에서 진입가에 도달하지 않았습니다")
    elif phase != "closed": warnings.append("구간이 끝날 때까지 청산되지 않았습니다 — 종료일 이후는 반영되지 않습니다")

    return {
        "plan_id": plan["id"], "name": plan["name"], "ticker": plan["ticker"], "side": plan["side"],
        "start": start, "end": end, "bars": len(bars),
        "entry_price": entry_price, "stop_price": stop_price, "r_unit": r_unit,
        "phase": phase, "entered": entered, "legs": legs,
        "realized_krw": realized_krw, "realized_r": realized_r,
        "open_quantity": open_quantity, "open_r": open_r, "total_r": total_r,
        "last_close": last_close, "warnings": warnings,
    }


def _record_fill(db, order_id: int) -> int | None:
    row = db.execute("SELECT * FROM broker_orders WHERE id=?", (order_id,)).fetchone()
    if row is None: raise ValueError("주문을 찾을 수 없습니다")
    order = dict(row)
    if order["trade_id"]: return None
    existing = db.execute("SELECT id FROM trades WHERE broker_order_id=? AND trade_date=?", (order["broker_order_id"], str(order["requested_at"])[:10])).fetchone() if order["broker_order_id"] else None
    if existing:
        db.execute("UPDATE broker_orders SET trade_id=?,updated_at=? WHERE id=?", (existing[0], _now(), order_id))
        return None
    quantity = float(order["filled_quantity"] or 0)
    if quantity <= 0: return None
    now = _now()
    trade_id = int(db.execute("INSERT INTO trades(ticker,side,trade_date,quantity,price,fee,tax,memo,created_at,broker_order_id) VALUES(?,?,?,?,?,?,?,?,?,?)", (order["ticker"], order["side"], str(order["requested_at"])[:10], quantity, float(order["filled_price"] or 0), float(order["fee"] or 0), float(order["tax"] or 0), f"KIS {order['env']} 자동주문 #{order_id}", now, order["broker_order_id"])).lastrowid)
    db.execute("UPDATE broker_orders SET trade_id=?,updated_at=? WHERE id=?", (trade_id, now, order_id))
    return trade_id


def record_fill(order_id: int, path=None) -> int | None:
    with db_session(path) as db:
        return _record_fill(db, int(order_id))


def _apply_fill(db, order_id: int, fill: Any) -> None:
    db.execute("UPDATE broker_orders SET status=?,filled_quantity=?,filled_price=?,fee=?,tax=?,payload=?,updated_at=? WHERE id=?",
               (fill.status, float(fill.quantity), float(fill.price) or None, float(fill.fee), float(fill.tax),
                json.dumps(fill.payload, ensure_ascii=False, default=str), _now(), order_id))
    if fill.status == "filled": _record_fill(db, order_id)


def manage_open_orders(broker=None, path=None, timeout_sec: float = 60.0, cancel_entries: bool = False) -> list[dict[str, Any]]:
    """접수된 채 체결되지 않고 있는 주문을 정리한다.

    진입은 **취소**한다 — 지정가가 안 붙었다는 건 가격이 이미 떠났다는 뜻이고, 계획에 없던 자리를
    추격 매수하지 않기 위해서다. 청산은 **시장가로 정정**한다 — 손절·익절은 값을 깎아서라도 반드시
    나가야 한다. 정정하면 KIS가 새 주문번호를 주므로 원주문은 닫고 새 주문 행을 남긴다."""
    if broker is None: raise ValueError("브로커가 설정되지 않았습니다")
    cutoff = (datetime.now() - timedelta(seconds=max(0.0, timeout_sec))).isoformat(timespec="seconds")
    with db_session(path) as db:
        pending = [dict(row) for row in db.execute(
            f"SELECT * FROM broker_orders WHERE status IN ({','.join('?' * len(OPEN_STATUSES))}) AND broker_order_id IS NOT NULL AND org_no IS NOT NULL AND requested_at <= ? ORDER BY id",
            (*OPEN_STATUSES, cutoff)).fetchall()]
    handled: list[dict[str, Any]] = []
    for order in pending:
        is_entry = order["leg"] == "entry"
        if is_entry and not cancel_entries and order["order_type"] == "market": continue
        remaining = float(order["quantity"]) - float(order["filled_quantity"] or 0)
        if remaining <= 0: continue
        action = "cancel" if is_entry else "revise"
        try:
            if action == "cancel":
                result = broker.cancel_order(order["org_no"], order["broker_order_id"], remaining)
            else:
                result = broker.revise_order(order["org_no"], order["broker_order_id"], remaining, "market", None)
            fill = broker.order_fill(order["broker_order_id"])
        except Exception as exc:
            with db_session(path) as db:
                db.execute("UPDATE broker_orders SET message=?,updated_at=? WHERE id=?", (f"{action} 실패 — {type(exc).__name__}: {exc}", _now(), order["id"]))
                handled.append(_order(db, order["id"]))
            continue
        with db_session(path) as db:
            if fill is not None: _apply_fill(db, order["id"], fill)
            if result.status == "rejected":
                db.execute("UPDATE broker_orders SET message=?,updated_at=? WHERE id=?", (f"{action} 거부 — {result.message}", _now(), order["id"]))
                handled.append(_order(db, order["id"]))
                continue
            note = "미체결 취소" if action == "cancel" else f"미체결 {remaining:g}주 시장가 정정"
            db.execute("UPDATE broker_orders SET status='cancelled',message=?,updated_at=? WHERE id=?", (f"{note} — {result.message}", _now(), order["id"]))
            handled.append(_order(db, order["id"]))
            if action == "revise":
                handled.append(_insert_order(db, {
                    "plan_id": order["plan_id"], "leg": order["leg"], "as_of": order["as_of"], "ticker": order["ticker"], "side": order["side"],
                    "quantity": remaining, "order_type": "market", "limit_price": None, "status": result.status, "env": order["env"],
                    "broker_order_id": result.broker_order_id, "org_no": result.org_no, "origin": order["origin"], "trigger_price": order["trigger_price"],
                    "replaces_order_id": order["id"], "message": f"미체결 정정 재주문 — {result.message}",
                    "payload": json.dumps(result.payload, ensure_ascii=False, default=str)}, _now()))
    return handled


def cancel_all_open_orders(broker=None, path=None) -> list[dict[str, Any]]:
    """지금 시장에 걸려 있는 우리 주문을 전부 취소한다(`mscr trade panic`)."""
    return manage_open_orders(broker, path, timeout_sec=0.0, cancel_entries=True)


def sync_orders(broker=None, path=None) -> list[dict[str, Any]]:
    if broker is None: raise ValueError("브로커가 설정되지 않았습니다")
    with db_session(path) as db:
        orders = [dict(row) for row in db.execute(f"SELECT * FROM broker_orders WHERE status IN ({','.join('?' * len(OPEN_STATUSES))}) AND broker_order_id IS NOT NULL ORDER BY id", OPEN_STATUSES).fetchall()]
    fills: list[tuple[int, Any, str | None]] = []
    for order in orders:
        try:
            fills.append((order["id"], broker.order_fill(order["broker_order_id"]), None))
        except Exception as exc:
            fills.append((order["id"], None, f"{type(exc).__name__}: {exc}"))
    updated: list[dict[str, Any]] = []
    with db_session(path) as db:
        for order_id, fill, error in fills:
            now = _now()
            if fill is None:
                db.execute("UPDATE broker_orders SET message=?,updated_at=? WHERE id=?", (error, now, order_id))
            else:
                _apply_fill(db, order_id, fill)
            updated.append(_order(db, order_id))
    return updated


def list_orders(limit: int = 200, path=None) -> list[dict[str, Any]]:
    with db_session(path) as db:
        return [dict(row) for row in db.execute(f"{ORDER_SELECT} ORDER BY o.requested_at DESC, o.id DESC LIMIT ?", (max(1, min(int(limit), 2000)),)).fetchall()]
