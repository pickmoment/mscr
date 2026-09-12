"""자동 실주문 안전장치 — 킬스위치, 일일 한도, 모의계좌 선행 검증.

원칙 하나로 움직인다: **막는 것은 신규 진입뿐이고 청산은 절대 막지 않는다.** 손절을 막는
안전장치는 안전장치가 아니기 때문이다. 전부 멈추려면 데몬 자체를 내리거나 `mscr trade panic`을 쓴다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .db import db_session

ENTRY_LEG = "entry"
# 주문이 실제로 시장에 나갔다고 볼 상태. dry_run·skipped·failed·rejected는 한도를 소모하지 않는다.
COUNTED_STATUSES = ("submitted", "partial", "filled", "cancelled")
DEFAULTS: dict[str, Any] = {
    "trade_kill_switch": 0,
    "trade_kill_reason": "",
    "trade_daily_entry_limit": 20,        # 하루 신규 진입 건수 상한. 0이면 무제한.
    "trade_daily_notional_limit_krw": 0,  # 하루 신규 진입 금액 상한. 0이면 무제한.
    "trade_require_paper_first": 1,       # 실계좌 진입 전에 같은 계획의 모의계좌 체결 이력을 요구한다.
}
_INT_KEYS = ("trade_kill_switch", "trade_daily_entry_limit", "trade_daily_notional_limit_krw", "trade_require_paper_first")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _read(db) -> dict[str, Any]:
    stored = {row["key"]: row["value"] for row in db.execute(
        f"SELECT key,value FROM settings WHERE key IN ({','.join('?' * len(DEFAULTS))})", tuple(DEFAULTS)).fetchall()}
    result: dict[str, Any] = {}
    for key, fallback in DEFAULTS.items():
        raw = stored.get(key)
        if key not in _INT_KEYS:
            result[key] = str(raw) if raw is not None else fallback
            continue
        try:
            result[key] = int(float(raw))
        except (TypeError, ValueError):
            result[key] = fallback
    return result


def settings(path=None) -> dict[str, Any]:
    with db_session(path) as db:
        return _read(db)


def save(path=None, **values: Any) -> dict[str, Any]:
    unknown = set(values) - set(DEFAULTS)
    if unknown: raise ValueError(f"알 수 없는 설정입니다: {', '.join(sorted(unknown))}")
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if value is None: continue
        if key in _INT_KEYS:
            try:
                number = int(float(value))
            except (TypeError, ValueError):
                raise ValueError(f"{key}는 숫자여야 합니다") from None
            if number < 0: raise ValueError(f"{key}는 0 이상이어야 합니다")
            cleaned[key] = number
        else:
            cleaned[key] = str(value)
    with db_session(path) as db:
        for key, value in cleaned.items():
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        return _read(db)


def engage(reason: str, path=None) -> dict[str, Any]:
    """킬스위치를 올린다. 신규 진입만 멈추고 이미 잡은 포지션의 청산은 계속 나간다."""
    return save(path, trade_kill_switch=1, trade_kill_reason=f"{_today()} {reason}".strip())


def release(path=None) -> dict[str, Any]:
    return save(path, trade_kill_switch=0, trade_kill_reason="")


def usage(env: str | None = None, path=None) -> dict[str, Any]:
    """오늘 나간 신규 진입의 건수와 금액. 청산은 한도 대상이 아니므로 세지 않는다."""
    clause = f"status IN ({','.join('?' * len(COUNTED_STATUSES))})"
    query = (f"SELECT COUNT(*) count, COALESCE(SUM(quantity * COALESCE(filled_price, limit_price, trigger_price, 0)), 0) notional "
             f"FROM broker_orders WHERE leg=? AND substr(requested_at,1,10)=? AND {clause}")
    params: list[Any] = [ENTRY_LEG, _today(), *COUNTED_STATUSES]
    if env:
        query += " AND env=?"
        params.append(env)
    with db_session(path) as db:
        row = db.execute(query, params).fetchone()
    return {"date": _today(), "entries": int(row["count"]), "notional_krw": float(row["notional"])}


def verified_plans(path=None) -> set[int]:
    """모의계좌에서 진입이 실제로 체결된 적 있는 계획. 실계좌 선행 검증 통과 여부의 근거다."""
    with db_session(path) as db:
        return {int(row[0]) for row in db.execute(
            "SELECT DISTINCT plan_id FROM broker_orders WHERE env='paper' AND leg=? AND status IN ('filled','partial') AND plan_id IS NOT NULL",
            (ENTRY_LEG,)).fetchall()}


def state(env: str | None = None, path=None) -> dict[str, Any]:
    current = settings(path)
    used = usage(env, path)
    entry_limit, notional_limit = current["trade_daily_entry_limit"], current["trade_daily_notional_limit_krw"]
    return {
        **current, "env": env, "usage": used,
        "entries_remaining": max(0, entry_limit - used["entries"]) if entry_limit else None,
        "notional_remaining_krw": max(0.0, notional_limit - used["notional_krw"]) if notional_limit else None,
        "blocked": bool(current["trade_kill_switch"]),
    }


def check(leg: str, env: str, plan_id: int | None, notional_krw: float, path=None,
          current: dict[str, Any] | None = None, used: dict[str, Any] | None = None, verified: set[int] | None = None) -> tuple[bool, str]:
    """진입 1건을 지금 내도 되는지 판정한다. 청산 레그는 어떤 이유로도 막지 않는다.

    데몬은 틱마다 호출하므로 current/used/verified를 미리 읽어 넘겨 DB 왕복을 줄일 수 있다."""
    if leg != ENTRY_LEG: return True, ""
    current = current if current is not None else settings(path)
    if current["trade_kill_switch"]:
        return False, f"킬스위치가 올라가 있습니다 — {current['trade_kill_reason'] or '사유 미기록'}"
    if env == "real" and current["trade_require_paper_first"]:
        verified = verified if verified is not None else verified_plans(path)
        if plan_id not in verified:
            return False, "모의계좌 선행 검증이 없습니다 — 같은 계획을 paper 환경에서 먼저 체결시키세요"
    used = used if used is not None else usage(env, path)
    entry_limit = current["trade_daily_entry_limit"]
    if entry_limit and used["entries"] >= entry_limit:
        return False, f"오늘 신규 진입 한도({entry_limit}건)를 모두 썼습니다"
    notional_limit = current["trade_daily_notional_limit_krw"]
    if notional_limit and used["notional_krw"] + notional_krw > notional_limit:
        return False, f"오늘 신규 진입 금액 한도({notional_limit:,.0f}원)를 넘습니다 — 사용 {used['notional_krw']:,.0f}원, 이번 주문 {notional_krw:,.0f}원"
    return True, ""
