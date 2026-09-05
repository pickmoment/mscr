from __future__ import annotations

from typing import Any

from .db import db_session
from .trading import plan_exposure

FILL_STATUSES = ("filled", "partial")
EXIT_LEGS = ("stop", "tp1", "tp2", "trailing")
PLANNED_EXIT_PRICE = {"stop": "stop_price", "tp1": "tp1_price", "tp2": "tp2_price"}  # 트레일링은 계획 가격이 없다
MIN_SAMPLE = 20
UNTAGGED = "(미분류)"
FAR_FUTURE = "9999-12-31"


def _fills(db, plan_ids: list[int]) -> dict[int, dict[str, dict[str, Any]]]:
    """계획×레그별 실체결 집계. 한 레그가 분할 체결되면 broker_orders 행이 여러 개 남으므로
    수량가중 평균가로 합친다. 모의 실행은 저장되지 않고 거부/실패 주문은 체결이 아니므로 제외한다."""
    if not plan_ids: return {}
    placeholders = ",".join("?" * len(plan_ids))
    rows = db.execute(
        f"SELECT plan_id,leg,as_of,requested_at,filled_quantity,filled_price,fee,tax FROM broker_orders"
        f" WHERE plan_id IN ({placeholders}) AND leg IS NOT NULL AND status IN {FILL_STATUSES} AND filled_quantity>0 ORDER BY id",
        plan_ids,
    ).fetchall()
    output: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        leg = output.setdefault(row["plan_id"], {}).setdefault(row["leg"], {"quantity": 0.0, "notional": 0.0, "fee": 0.0, "tax": 0.0, "date": None})
        quantity, price = float(row["filled_quantity"]), float(row["filled_price"] or 0.0)
        leg["quantity"] += quantity
        leg["notional"] += quantity * price
        leg["fee"] += float(row["fee"] or 0.0)
        leg["tax"] += float(row["tax"] or 0.0)
        day = (row["as_of"] or row["requested_at"] or "")[:10]
        if day and (leg["date"] is None or day < leg["date"]): leg["date"] = day
    for legs in output.values():
        for leg in legs.values():
            leg["price"] = leg["notional"] / leg["quantity"] if leg["quantity"] else None
    return output


def _slippage_pct(planned: float | None, actual: float | None, worse_when_higher: bool) -> float | None:
    """계획가 대비 체결가 손해율(%). 부호는 항상 '양수 = 불리하게 체결'이다."""
    if not planned or actual is None: return None
    diff = actual - planned if worse_when_higher else planned - actual
    return diff / planned * 100


def _excursion(db, ticker: str, start: str, end: str | None) -> dict[str, Any]:
    row = db.execute(
        "SELECT MIN(low) lo, MAX(high) hi, COUNT(*) bars FROM daily_bars WHERE ticker=? AND source='krx_snapshot' AND date>=? AND date<=?",
        (ticker, start, end or FAR_FUTURE),
    ).fetchone()
    return {"low": row["lo"], "high": row["hi"], "bars": int(row["bars"] or 0)}


def plan_results(path=None) -> list[dict[str, Any]]:
    """계획 1건씩의 R 결산(복기). 진입이 실제 체결된 계획만 대상으로, 계획가가 아니라 실제 체결가로
    손익을 계산하고 R(=|계획 진입가 - 손절가|, 주당) 단위로 환산한다. R 분모는 계획을 세운 시점에
    고정되므로 이후 체결가가 밀려도 바뀌지 않는다.
    단위: *_pct 는 0~100 퍼센트(1.5 = 1.5%), *_r 은 배율 그대로의 R 값, *_krw 는 원."""
    exposures = {int(row["plan_id"]): row for row in plan_exposure(path)}
    output: list[dict[str, Any]] = []
    with db_session(path) as db:
        fills = _fills(db, list(exposures))
        tickers = sorted({row["ticker"] for row in exposures.values()})
        names = {}
        if tickers:
            names = {row["ticker"]: row["name"] for row in db.execute(f"SELECT ticker,name FROM instruments WHERE ticker IN ({','.join('?' * len(tickers))})", tickers).fetchall()}
        for plan_id, plan in exposures.items():
            legs = fills.get(plan_id, {})
            entry = legs.get("entry")
            if not entry or entry["quantity"] <= 0: continue
            long = plan["side"] == "buy"
            direction = 1.0 if long else -1.0
            planned_entry, stop_price = float(plan["entry_price"]), float(plan["stop_price"])
            r_unit = abs(planned_entry - stop_price)
            entry_price, entry_quantity = entry["price"], entry["quantity"]
            entry_cost = entry["fee"] + entry["tax"]
            position_r = r_unit * entry_quantity  # 포지션 전체를 1R로 보는 분모
            exits: list[dict[str, Any]] = []
            exited_quantity = 0.0
            for leg in EXIT_LEGS:
                info = legs.get(leg)
                if not info or info["quantity"] <= 0: continue
                quantity, price = info["quantity"], info["price"]
                costs = info["fee"] + info["tax"] + entry_cost * (quantity / entry_quantity)
                realized = (price - entry_price) * quantity * direction - costs
                planned_exit = plan.get(PLANNED_EXIT_PRICE[leg]) if leg in PLANNED_EXIT_PRICE else None
                exits.append({
                    "leg": leg, "date": info["date"], "quantity": quantity, "price": price,
                    "slippage_pct": _slippage_pct(planned_exit, price, not long),
                    "realized_krw": realized,
                    "realized_r": realized / (r_unit * quantity) if r_unit and quantity else None,
                })
                exited_quantity += quantity
            exits.sort(key=lambda row: (row["date"] or "", row["leg"]))
            realized_krw = sum(row["realized_krw"] for row in exits)
            # 청산 레그별 realized_r은 '그 물량 기준' R이라 그대로 더하면 분할 청산이 과대 계상된다.
            # 합계는 포지션 전체를 분모로 환산해 전량 +1R 청산이 정확히 +1R이 되도록 한다.
            realized_r = realized_krw / position_r if position_r else None
            closed = plan["phase"] == "closed"
            open_quantity = 0.0 if closed else max(0.0, entry_quantity - exited_quantity)
            close = plan["close"]
            open_r = (close - entry_price) * open_quantity * direction / position_r if open_quantity > 0 and close is not None and position_r else None
            exit_date = exits[-1]["date"] if exits else None
            entry_date = entry["date"]
            excursion = _excursion(db, plan["ticker"], entry_date or "", exit_date if closed else None)
            low, high = excursion["low"], excursion["high"]
            mae_r = mfe_r = None
            if r_unit and low is not None and high is not None:
                adverse = (low - entry_price) if long else (entry_price - high)
                favourable = (high - entry_price) if long else (entry_price - low)
                mae_r, mfe_r = min(0.0, adverse / r_unit), max(0.0, favourable / r_unit)
            output.append({
                "plan_id": plan_id, "name": plan["name"], "ticker": plan["ticker"], "ticker_name": names.get(plan["ticker"], plan["ticker"]),
                "side": plan["side"], "setup": plan["setup"], "status": "closed" if closed else "open",
                "planned_entry": planned_entry, "entry_price": entry_price, "entry_quantity": entry_quantity, "entry_date": entry_date,
                "entry_slippage_pct": _slippage_pct(planned_entry, entry_price, long),
                "stop_price": stop_price, "r_unit": r_unit,
                "exits": exits,
                "realized_krw": realized_krw, "realized_r": realized_r,
                "open_quantity": open_quantity, "open_r": open_r,
                "total_r": None if realized_r is None and open_r is None else (realized_r or 0.0) + (open_r or 0.0),
                "mae_r": mae_r, "mfe_r": mfe_r,
                "days_held": excursion["bars"], "exit_date": exit_date,
            })
    output.sort(key=lambda row: (row["entry_date"] or "", row["plan_id"]), reverse=True)
    return output


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _group(label: str, key: str, scores: list[float]) -> dict[str, Any]:
    wins = [value for value in scores if value > 0]
    return {key: label, "trades": len(scores), "win_rate": len(wins) / len(scores) * 100 if scores else None, "avg_r": _mean(scores), "total_r": sum(scores)}


def stats(path=None) -> dict[str, Any]:
    """복기 통계. 성과 지표는 청산이 끝난 계획만으로 집계하고, 진행 중인 계획은 open 항목에만 담는다.
    단위: win_rate 와 *_pct 는 0~100 퍼센트(62.5 = 62.5%), *_r 은 배율 그대로의 R 값."""
    rows = plan_results(path)
    closed = [row for row in rows if row["status"] == "closed" and row["realized_r"] is not None]
    # 결산 순서는 청산일 기준(연속 손실·낙폭·자산곡선 모두 시간순 의미를 가진다)
    closed.sort(key=lambda row: (row["exit_date"] or row["entry_date"] or "", row["plan_id"]))
    scores = [row["realized_r"] for row in closed]
    wins = [value for value in scores if value > 0]
    losses = [value for value in scores if value < 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    win_rate = len(wins) / len(scores) * 100 if scores else None
    avg_win_r, avg_loss_r = _mean(wins), _mean(losses)
    streak = worst_streak = 0
    for value in scores:
        streak = streak + 1 if value < 0 else 0
        worst_streak = max(worst_streak, streak)
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    curve: list[dict[str, Any]] = []
    for row in closed:
        cumulative += row["realized_r"]
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
        date = row["exit_date"] or row["entry_date"]
        if curve and curve[-1]["date"] == date:
            curve[-1]["cumulative_r"] = cumulative
        else:
            curve.append({"date": date, "cumulative_r": cumulative})
    by_setup: dict[str, list[float]] = {}
    by_month: dict[str, list[float]] = {}
    for row in closed:
        by_setup.setdefault(row["setup"] or UNTAGGED, []).append(row["realized_r"])
        month = (row["exit_date"] or row["entry_date"] or "")[:7]
        if month: by_month.setdefault(month, []).append(row["realized_r"])
    open_rows = [row for row in rows if row["status"] == "open"]
    with db_session(path) as db:
        recorded_costs = db.execute(f"SELECT COALESCE(SUM(fee+tax),0) FROM broker_orders WHERE status IN {FILL_STATUSES} AND filled_quantity>0").fetchone()[0]
    warnings: list[str] = []
    if len(closed) < MIN_SAMPLE:
        warnings.append(f"청산 완료 표본이 {len(closed)}건으로 {MIN_SAMPLE}건 미만이라 통계 신뢰도가 낮습니다")
    if rows and not recorded_costs:
        warnings.append("KIS 체결 조회가 수수료·세금을 돌려주지 않아 비용이 0으로 기록돼 있습니다. 실제 R은 표시값보다 조금 나쁩니다")
    return {
        "trades": len(closed), "wins": len(wins), "losses": len(losses), "win_rate": win_rate,
        "avg_r": _mean(scores), "total_r": sum(scores),
        "expectancy_r": (len(wins) * (avg_win_r or 0.0) + len(losses) * (avg_loss_r or 0.0)) / len(scores) if scores else None,
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "avg_win_r": avg_win_r, "avg_loss_r": avg_loss_r,
        "max_consecutive_losses": worst_streak, "max_drawdown_r": drawdown,
        "avg_days_held": _mean([float(row["days_held"]) for row in closed]),
        "avg_entry_slippage_pct": _mean([row["entry_slippage_pct"] for row in closed if row["entry_slippage_pct"] is not None]),
        "equity_curve": curve,
        "by_setup": sorted([_group(label, "setup", scores) for label, scores in by_setup.items()], key=lambda row: row["total_r"], reverse=True),
        "by_month": sorted([_group(label, "month", scores) for label, scores in by_month.items()], key=lambda row: row["month"]),
        "open": {"count": len(open_rows), "total_open_r": sum(row["open_r"] for row in open_rows if row["open_r"] is not None)},
        "warnings": warnings,
    }
