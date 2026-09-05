from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .db import db_session
from .portfolio import snapshot

SIDES = {"buy", "sell"}
ORDER_TYPES = {"limit", "market"}
OPEN_STATUSES = ("submitted", "partial")
ACTIVE_LEG_STATUSES = ("dry_run", "submitted", "partial", "filled")
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


def _plan_legs(db, plan_ids: list[int]) -> dict[int, dict[str, dict[str, Any]]]:
    """Latest broker_orders row per (plan_id, leg), keyed by plan_id then leg."""
    if not plan_ids: return {}
    rows = db.execute(f"SELECT * FROM broker_orders WHERE plan_id IN ({','.join('?' * len(plan_ids))}) AND leg IS NOT NULL ORDER BY id", plan_ids).fetchall()
    result: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        order = dict(row)
        result.setdefault(order["plan_id"], {})[order["leg"]] = order
    return result


def _latest_bar(db, ticker: str) -> dict[str, Any] | None:
    row = db.execute("SELECT date,open,high,low,close,volume FROM daily_bars WHERE ticker=? AND source='krx_snapshot' ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
    return dict(row) if row else None


def _trailing_reference(db, ticker: str, side: str, since_date: str) -> float | None:
    column = "MAX(high)" if side == "buy" else "MIN(low)"
    value = db.execute(f"SELECT {column} FROM daily_bars WHERE ticker=? AND source='krx_snapshot' AND date>=?", (ticker, since_date)).fetchone()[0]
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


def _plan_evaluation(db, plan: dict[str, Any], legs: dict[str, dict[str, Any]], latest_date: str | None) -> dict[str, Any]:
    ticker = plan["ticker"]
    bar = _latest_bar(db, ticker)
    if bar is None:
        return {"phase": "waiting_entry", "next_leg": None, "triggered": False, "reason": "일봉 데이터 없음", "as_of": None, "close": None, "order_side": None, "order_quantity": None}
    long = plan["side"] == "buy"
    exit_side = "sell" if long else "buy"
    quantity = float(plan["quantity"])
    close = float(bar["close"])
    stale = latest_date is not None and bar["date"] != latest_date

    done = {leg: info["status"] in ACTIVE_LEG_STATUSES for leg, info in legs.items()}
    entry_done, stop_done = done.get("entry", False), done.get("stop", False)
    tp1_done, tp2_done, trailing_done = done.get("tp1", False), done.get("tp2", False), done.get("trailing", False)

    if stop_done or trailing_done:
        return {"phase": "closed", "next_leg": None, "triggered": False, "reason": "청산 완료", "as_of": bar["date"], "close": close, "order_side": None, "order_quantity": None}

    if not entry_done:
        hit = bar["high"] >= plan["entry_price"] if long else bar["low"] <= plan["entry_price"]
        reason = "일봉이 오래되었습니다" if stale else ("진입가 돌파 → 진입 주문 대상" if hit else f"진입가 대기 중 (진입가 {plan['entry_price']:g}, 현재가 {close:g})")
        return {"phase": "waiting_entry", "next_leg": "entry", "triggered": hit, "reason": reason, "as_of": bar["date"], "close": close, "order_side": plan["side"], "order_quantity": quantity}

    tp1_qty, tp2_qty, trailing_qty = _leg_quantities(quantity, plan["tp1_ratio"], plan["tp2_ratio"])
    # 정수 배분으로 수량이 0이 된 익절 레그는 0주 주문이 되므로 주문 대상에서 건너뛴다(다음 레그로 진행).
    tp1_pending, tp2_pending = not tp1_done and tp1_qty > 0, not tp2_done and tp2_qty > 0
    remaining_qty = quantity - (tp1_qty if tp1_done else 0.0) - (tp2_qty if tp2_done else 0.0)
    if remaining_qty <= 0:
        return {"phase": "closed", "next_leg": None, "triggered": False, "reason": "청산 완료 — 남은 배정 수량 없음", "as_of": bar["date"], "close": close, "order_side": None, "order_quantity": None}
    phase = "holding" if tp1_pending else "tp1_done" if tp2_pending else "trailing"

    stop_hit = bar["low"] <= plan["stop_price"] if long else bar["high"] >= plan["stop_price"]
    if stop_hit:
        reason = "일봉이 오래되었습니다" if stale else "손절가 이탈 → 즉시 청산"
        return {"phase": phase, "next_leg": "stop", "triggered": True, "reason": reason, "as_of": bar["date"], "close": close, "order_side": exit_side, "order_quantity": remaining_qty}

    if tp1_pending:
        hit = bar["high"] >= plan["tp1_price"] if long else bar["low"] <= plan["tp1_price"]
        reason = "일봉이 오래되었습니다" if stale else ("1차 목표가 도달 → 1차 익절" if hit else f"1차 목표가 대기 중 ({plan['tp1_price']:g})")
        return {"phase": "holding", "next_leg": "tp1", "triggered": hit, "reason": reason, "as_of": bar["date"], "close": close, "order_side": exit_side, "order_quantity": tp1_qty}

    if tp2_pending:
        hit = bar["high"] >= plan["tp2_price"] if long else bar["low"] <= plan["tp2_price"]
        reason = "일봉이 오래되었습니다" if stale else ("2차 목표가 도달 → 2차 익절" if hit else f"2차 목표가 대기 중 ({plan['tp2_price']:g})")
        return {"phase": "tp1_done", "next_leg": "tp2", "triggered": hit, "reason": reason, "as_of": bar["date"], "close": close, "order_side": exit_side, "order_quantity": tp2_qty}

    since_date = str((legs["tp2"] if tp2_done else legs["tp1"] if tp1_done else legs["entry"])["as_of"])
    reference = _trailing_reference(db, ticker, plan["side"], since_date)
    if reference is None:
        return {"phase": "trailing", "next_leg": "trailing", "triggered": False, "reason": "트레일링 기준 데이터 없음", "as_of": bar["date"], "close": close, "order_side": exit_side, "order_quantity": trailing_qty}
    trail_level = reference * (1 - plan["tp3_trailing_pct"] / 100) if long else reference * (1 + plan["tp3_trailing_pct"] / 100)
    hit = bar["low"] <= trail_level if long else bar["high"] >= trail_level
    reason = "일봉이 오래되었습니다" if stale else (f"트레일링 스탑 이탈({trail_level:g}) → 전량 청산" if hit else f"트레일링 스탑 감시 중 (기준 {reference:g}, 이탈가 {trail_level:g})")
    return {"phase": "trailing", "next_leg": "trailing", "triggered": hit, "reason": reason, "as_of": bar["date"], "close": close, "order_side": exit_side, "order_quantity": trailing_qty}


def evaluate_plans(plan_ids: list[int] | None = None, path=None) -> list[dict[str, Any]]:
    wanted = {int(value) for value in plan_ids} if plan_ids else None
    with db_session(path) as db:
        plans = [_plan(row) for row in db.execute("SELECT * FROM trade_plans ORDER BY name").fetchall() if wanted is None or row["id"] in wanted]
        latest_date = db.execute("SELECT MAX(date) FROM daily_bars WHERE source='krx_snapshot'").fetchone()[0]
        legs_by_plan = _plan_legs(db, [plan["id"] for plan in plans])
        output = []
        for plan in plans:
            evaluation = _plan_evaluation(db, plan, legs_by_plan.get(plan["id"], {}), latest_date)
            if evaluation["triggered"] and not plan["enabled"]:
                evaluation = {**evaluation, "triggered": False, "reason": "비활성 계획"}
            output.append({"plan_id": plan["id"], "name": plan["name"], "ticker": plan["ticker"], "side": plan["side"], **evaluation})
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
            quantity = float(plan["quantity"])
            tp1_qty, tp2_qty, _ = _leg_quantities(quantity, plan["tp1_ratio"], plan["tp2_ratio"])
            entered = done.get("entry", False)
            closed = done.get("stop", False) or done.get("trailing", False)
            remaining = 0.0 if closed or not entered else quantity - (tp1_qty if done.get("tp1") else 0.0) - (tp2_qty if done.get("tp2") else 0.0)
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


def run_plans(broker=None, dry_run: bool = True, plan_ids: list[int] | None = None, path=None) -> list[dict[str, Any]]:
    if not dry_run and broker is None: raise ValueError("브로커가 설정되지 않았습니다")
    env = str(getattr(broker, "env", "") or "")
    evaluations = {item["plan_id"]: item for item in evaluate_plans(plan_ids, path)}
    plans = {plan["id"]: plan for plan in list_plans(path)}
    holdings = {row["ticker"]: float(row["quantity"]) for row in snapshot(path)["positions"]}
    now = _now()
    recorded: list[dict[str, Any]] = []
    with db_session(path) as db:
        latest = db.execute("SELECT MAX(date) FROM daily_bars WHERE source='krx_snapshot'").fetchone()[0]
        reserved = {row[0]: float(row[1] or 0) for row in db.execute(f"SELECT ticker, SUM(quantity - filled_quantity) FROM broker_orders WHERE side='sell' AND status IN ({','.join('?' * len(OPEN_STATUSES))}) GROUP BY ticker", OPEN_STATUSES).fetchall()}
        for plan_id, evaluation in evaluations.items():
            plan = plans.get(plan_id)
            if plan is None or not plan["enabled"] or not evaluation["triggered"] or evaluation["next_leg"] is None: continue
            leg = evaluation["next_leg"]
            order_side = evaluation["order_side"]
            quantity = float(evaluation["order_quantity"])
            order_type = plan["order_type"] if leg == "entry" else "market"
            limit_price = plan["limit_price"] if leg == "entry" else None
            order = {"status": "dry_run" if dry_run else "submitted", "broker_order_id": None, "message": "모의 실행 — 저장되지 않는 미리보기입니다" if dry_run else "", "payload": None}
            available = holdings.get(plan["ticker"], 0.0) - reserved.get(plan["ticker"], 0.0)
            if leg != "entry" and order_side == "sell" and quantity > available + 1e-9:
                order = {"status": "skipped", "broker_order_id": None, "message": f"보유 수량 부족: 주문 가능 {available:g}, 요청 {quantity:g}", "payload": None}
            elif evaluation["as_of"] != latest:
                order = {"status": "skipped", "broker_order_id": None, "message": f"일봉이 오래되었습니다: 종목 {evaluation['as_of']}, 최신 {latest}", "payload": None}
            elif not dry_run:
                try:
                    result = broker.submit_order(plan["ticker"], order_side, quantity, order_type, limit_price)
                    order = {"status": result.status, "broker_order_id": result.broker_order_id, "message": result.message, "payload": json.dumps(result.payload, ensure_ascii=False, default=str)}
                except Exception as exc:
                    order = {"status": "failed", "broker_order_id": None, "message": f"{type(exc).__name__}: {exc}", "payload": None}
            if dry_run:
                recorded.append({"id": None, "plan_id": plan["id"], "plan_name": plan["name"], "leg": leg, "as_of": evaluation["as_of"], "ticker": plan["ticker"], "side": order_side, "quantity": quantity, "order_type": order_type, "limit_price": limit_price, "status": order["status"], "env": env, "broker_order_id": None, "filled_quantity": 0.0, "filled_price": None, "fee": 0.0, "tax": 0.0, "trade_id": None, "message": order["message"], "payload": order["payload"], "requested_at": now, "updated_at": now})
                continue
            if order_side == "sell" and order["status"] in OPEN_STATUSES: reserved[plan["ticker"]] = reserved.get(plan["ticker"], 0.0) + quantity
            try:
                cursor = db.execute(
                    "INSERT INTO broker_orders(plan_id,leg,as_of,ticker,side,quantity,order_type,limit_price,status,env,broker_order_id,message,payload,requested_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (plan["id"], leg, evaluation["as_of"], plan["ticker"], order_side, quantity, order_type, limit_price, order["status"], env, order["broker_order_id"], order["message"], order["payload"], now, now))
            except sqlite3.IntegrityError:
                cursor = db.execute(
                    "INSERT INTO broker_orders(plan_id,leg,as_of,ticker,side,quantity,order_type,limit_price,status,env,broker_order_id,message,payload,requested_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?)",
                    (plan["id"], leg, evaluation["as_of"], plan["ticker"], order_side, quantity, order_type, limit_price, "failed", env, f"중복 주문번호 {order['broker_order_id']} — 주문 기록만 남깁니다", order["payload"], now, now))
            recorded.append(_order(db, int(cursor.lastrowid)))
    return recorded


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
                db.execute("UPDATE broker_orders SET status=?,filled_quantity=?,filled_price=?,fee=?,tax=?,payload=?,updated_at=? WHERE id=?", (fill.status, float(fill.quantity), float(fill.price) or None, float(fill.fee), float(fill.tax), json.dumps(fill.payload, ensure_ascii=False, default=str), now, order_id))
                if fill.status == "filled": _record_fill(db, order_id)
            updated.append(_order(db, order_id))
    return updated


def list_orders(limit: int = 200, path=None) -> list[dict[str, Any]]:
    with db_session(path) as db:
        return [dict(row) for row in db.execute(f"{ORDER_SELECT} ORDER BY o.requested_at DESC, o.id DESC LIMIT ?", (max(1, min(int(limit), 2000)),)).fetchall()]
