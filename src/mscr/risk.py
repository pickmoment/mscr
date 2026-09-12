from __future__ import annotations

from typing import Any

from .db import db_session
from . import market
from .portfolio import snapshot
from .trading import plan_exposure

DEFAULT_RISK_PER_TRADE_PCT = 1.0
DEFAULT_MAX_HEAT_PCT = 6.0
_KEYS = {"risk_per_trade_pct": DEFAULT_RISK_PER_TRADE_PCT, "max_portfolio_heat_pct": DEFAULT_MAX_HEAT_PCT}


def limits(path=None) -> dict[str, float]:
    with db_session(path) as db:
        stored = {row["key"]: row["value"] for row in db.execute(f"SELECT key,value FROM settings WHERE key IN ({','.join('?' * len(_KEYS))})", tuple(_KEYS)).fetchall()}
    result: dict[str, float] = {}
    for key, fallback in _KEYS.items():
        try:
            result[key] = float(stored[key])
        except (KeyError, TypeError, ValueError):
            result[key] = fallback
    return result


def save_limits(risk_per_trade_pct: float, max_portfolio_heat_pct: float, path=None) -> dict[str, float]:
    values = {"risk_per_trade_pct": float(risk_per_trade_pct), "max_portfolio_heat_pct": float(max_portfolio_heat_pct)}
    for key, value in values.items():
        if not 0 < value <= 100: raise ValueError(f"{key}는 0 초과 100 이하여야 합니다")
    if values["risk_per_trade_pct"] > values["max_portfolio_heat_pct"]:
        raise ValueError("1건 리스크 한도가 포트폴리오 히트 한도보다 클 수 없습니다")
    with db_session(path) as db:
        for key, value in values.items():
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    return values


def _plan_risk(plan: dict[str, Any]) -> dict[str, Any]:
    """계획 1건이 지금 시장에 걸어 둔 손실 금액.

    진입 전이면 계획대로 체결됐을 때의 손실(진입가−손절가), 진입 후면 남은 수량이 지금 손절당했을 때의
    손실(현재가−손절가)이다. 손절가가 이미 현재가 너머로 넘어간(이익 구간에 손절을 올려 둔) 계획은 0이며,
    갭 하락은 반영하지 않으므로 실제 손실은 이보다 클 수 있다."""
    long = plan["side"] == "buy"
    entry, stop = float(plan["entry_price"]), float(plan["stop_price"])
    unit_initial = abs(entry - stop)
    if not plan["enabled"] and not plan["entered"]:
        return {"state": "disabled", "quantity": 0.0, "risk_krw": 0.0, "initial_risk_krw": 0.0}
    if plan["closed"]:
        return {"state": "closed", "quantity": 0.0, "risk_krw": 0.0, "initial_risk_krw": 0.0}
    if not plan["entered"]:
        quantity = float(plan["quantity"])
        return {"state": "pending", "quantity": quantity, "risk_krw": unit_initial * quantity, "initial_risk_krw": unit_initial * quantity}
    quantity = float(plan["remaining_quantity"])
    reference = plan["close"] if plan["close"] is not None else entry
    unit_now = max(0.0, (reference - stop) if long else (stop - reference))
    return {"state": "open", "quantity": quantity, "risk_krw": unit_now * quantity, "initial_risk_krw": unit_initial * quantity}


def heat(path=None) -> dict[str, Any]:
    """포트폴리오 전체가 지금 감수 중인 손실 합계(히트)와 남은 리스크 여유.

    계획 1건의 최대 손실 금액만 보면 계획이 여러 개 동시에 열렸을 때의 합산 손실을 놓친다."""
    configured = limits(path)
    portfolio = snapshot(path)
    equity = float(portfolio["total_assets"])
    # 계획·손절은 국내 전용 기능이다. 미국 모드에서는 계획이 없는 상태의 히트(=0)를 보여준다.
    trading_market = market.active().trading
    plans = plan_exposure(path) if trading_market else []
    rows: list[dict[str, Any]] = []
    open_risk = pending_risk = 0.0
    for plan in plans:
        risk = _plan_risk(plan)
        if risk["state"] == "open": open_risk += risk["risk_krw"]
        elif risk["state"] == "pending": pending_risk += risk["risk_krw"]
        rows.append({
            "plan_id": plan["id"], "name": plan["name"], "ticker": plan["ticker"], "side": plan["side"],
            "setup": plan.get("setup"), "phase": plan["phase"], "enabled": plan["enabled"],
            "entry_price": plan["entry_price"], "stop_price": plan["stop_price"], "close": plan["close"],
            "risk_pct": (risk["risk_krw"] / equity * 100) if equity > 0 else None, **risk,
        })
    covered = {plan["ticker"] for plan in plans if plan["entered"] and plan["remaining_quantity"] > 0}
    unprotected = [
        {"ticker": position["ticker"], "name": position["name"], "quantity": position["quantity"], "market_value": position["market_value"], "weight": position["weight"]}
        for position in portfolio["positions"] if position["ticker"] not in covered]
    total_risk = open_risk + pending_risk
    heat_limit = equity * configured["max_portfolio_heat_pct"] / 100
    per_trade = equity * configured["risk_per_trade_pct"] / 100
    remaining = max(0.0, heat_limit - total_risk)
    warnings: list[str] = []
    if equity <= 0:
        warnings.append("총자산이 0입니다 — 포트폴리오 원장과 현금을 입력하면 리스크 비율이 계산됩니다")
    elif total_risk > heat_limit:
        warnings.append(f"포트폴리오 히트가 한도를 넘었습니다: {total_risk / equity * 100:.2f}% > {configured['max_portfolio_heat_pct']:g}%")
    if unprotected and trading_market:
        warnings.append(f"손절 계획이 없는 보유 종목 {len(unprotected)}건 — 손실 한도가 걸려 있지 않습니다")
    return {
        "as_of": max((plan["as_of"] for plan in plans if plan["as_of"]), default=None),
        "equity": equity, "cash_krw": portfolio["cash"], "market_value": portfolio["total_market_value"],
        "limits": configured,
        "open_risk_krw": open_risk, "pending_risk_krw": pending_risk, "total_risk_krw": total_risk,
        "heat_pct": (total_risk / equity * 100) if equity > 0 else None,
        "open_heat_pct": (open_risk / equity * 100) if equity > 0 else None,
        "budget": {
            "per_trade_krw": per_trade, "heat_limit_krw": heat_limit, "remaining_krw": remaining,
            "suggested_max_loss": min(per_trade, remaining) if equity > 0 else None,
        },
        "over_limit": equity > 0 and total_risk > heat_limit,
        "plans": rows, "unprotected": unprotected, "warnings": warnings,
    }
