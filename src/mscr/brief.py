"""장 마감 브리핑 — "오늘 뭘 봐야 하나"에 한 번에 답하는 페이로드.

신호 로그(screen_signals), 계획 단계, 관심종목 목표가, 보유 종목의 손절 커버리지, 포트폴리오
히트를 한곳에 모은다. 스크리너 수식은 다시 돌리지 않는다 — 이미 적재된 로그와 스냅샷만 읽으므로
장 마감 후 언제 실행해도 결과가 같고 비용도 일정하다.
"""

from __future__ import annotations

import json
from typing import Any

from . import market, risk, signals, trading, watchlist
from .db import db_session
from .market import bar_source
from .portfolio import snapshot

NEAR_PCT = 3.0
STREAK_LEADERS = 5
RENDER_ROWS = 10
FAR_FUTURE = "9999-12-31"
TRACKED_SCREENS_KEY = "brief_screen_ids"
PHASE_LABELS = {"waiting_entry": "진입대기", "holding": "보유중", "tp1_done": "1차익절", "trailing": "트레일링", "closed": "청산"}


def _tracked_key() -> str:
    """추적 프리셋 설정은 시장 모드별로 따로 저장한다."""
    mkt = market.active()
    return TRACKED_SCREENS_KEY if mkt.region == market.KR.region else f"{TRACKED_SCREENS_KEY}_{mkt.key}"


def tracked_screen_ids(path=None) -> list[int] | None:
    """브리핑이 추적할 프리셋 id 목록. 설정한 적이 없으면 `None`(전체 프리셋 추적)."""
    with db_session(path) as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (_tracked_key(),)).fetchone()
    if row is None: return None
    try:
        return [int(value) for value in json.loads(row["value"])]
    except (TypeError, ValueError):
        return None


def set_tracked_screen_ids(ids: list[int] | None, path=None) -> None:
    """`None`은 필터를 지우고 전체 프리셋을 다시 추적한다. 빈 리스트는 '아무 프리셋도 추적하지 않음'으로 그대로 저장된다."""
    with db_session(path) as db:
        if ids is None:
            db.execute("DELETE FROM settings WHERE key=?", (_tracked_key(),))
        else:
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (_tracked_key(), json.dumps(sorted({int(value) for value in ids}))))


def _recent_bars(db, tickers: list[str], bound: str) -> dict[str, list[dict[str, Any]]]:
    """티커별 기준일 이하 최근 2개 일봉. 종목마다 질의하면 유니버스 크기만큼 왕복하므로 한 번에 묶어 읽는다."""
    if not tickers: return {}
    holes = ",".join("?" * len(tickers))
    rows = db.execute(
        "SELECT ticker,date,close FROM (SELECT ticker,date,close,ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn "
        f"FROM daily_bars WHERE source='{bar_source()}' AND date<=? AND ticker IN ({holes})) WHERE rn<=2 ORDER BY ticker,date DESC",
        (bound, *tickers)).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["ticker"], []).append(dict(row))
    return grouped


def _signal_row(row: dict[str, Any], bars: dict[str, list[dict[str, Any]]], streak: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ticker = row["ticker"]
    series = bars.get(ticker, [])
    # 로그에 남은 close는 수집 당시 값이므로, 기준일 시세가 있으면 그쪽을 쓴다.
    close = series[0]["close"] if series else row["close"]
    prior = series[1]["close"] if len(series) > 1 else None
    return {
        "ticker": ticker, "name": row["name"] or ticker, "market": row["market"],
        "rank": row["rank"], "score": row["score"], "close": close,
        "change_pct": (close / prior - 1) * 100 if close and prior else None,
        "streak_days": (streak.get(ticker) or {}).get("days"),
    }


def _screen_logs(as_of: str | None, path, screen_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """프리셋별 '기준일 이하 가장 최근 수집일' 한 줄. 수집이 밀린 프리셋도 자기 최신일로 남는다.
    `screen_ids`가 빈 리스트면(설정에서 전부 해제) 아무 프리셋도 보지 않는다 — `None`(미설정)과 구분된다."""
    if screen_ids is not None and not screen_ids: return []
    clause, params = "", [as_of or FAR_FUTURE]
    if screen_ids:
        clause = f" AND r.screen_id IN ({','.join('?' * len(screen_ids))})"
        params.extend(int(value) for value in screen_ids)
    with db_session(path) as db:
        runs = db.execute(
            "SELECT r.screen_id, s.name, r.date, r.matched FROM screen_runs r JOIN screens s ON s.id=r.screen_id "
            f"WHERE s.region=? AND r.date=(SELECT MAX(d.date) FROM screen_runs d WHERE d.screen_id=r.screen_id AND d.date<=?){clause} ORDER BY s.name",
            [market.region(), *params]).fetchall()
    logs = []
    for run in runs:
        logs.append({
            "screen_id": run["screen_id"], "name": run["name"], "date": run["date"], "matched": run["matched"],
            "diff": signals.diff(run["screen_id"], run["date"], path), "streaks": signals.streaks(run["screen_id"], run["date"], path),
        })
    return logs


def _plan_rows(path) -> list[dict[str, Any]]:
    evaluations = {row["plan_id"]: row for row in trading.evaluate_plans(path=path)}
    rows = []
    for plan in trading.list_plans(path):
        evaluation = evaluations.get(plan["id"])
        if evaluation is None or not plan["enabled"] or evaluation["phase"] == "closed": continue
        long, close = plan["side"] == "buy", evaluation["close"]
        waiting = evaluation["phase"] == "waiting_entry"
        entry, stop = float(plan["entry_price"]), float(plan["stop_price"])
        distance = ((entry - close) if long else (close - entry)) / close * 100 if waiting and close else None
        stop_distance = ((close - stop) if long else (stop - close)) / close * 100 if not waiting and close else None
        relevant = distance if waiting else stop_distance
        rows.append({
            "plan_id": plan["id"], "name": plan["name"], "ticker": plan["ticker"], "ticker_name": plan["ticker"],
            "side": plan["side"], "setup": plan.get("setup"), "phase": evaluation["phase"],
            "triggered": bool(evaluation["triggered"]), "reason": evaluation["reason"], "close": close,
            "entry_price": entry, "stop_price": stop, "distance_pct": distance, "stop_distance_pct": stop_distance,
            "near": bool(evaluation["triggered"] or (relevant is not None and abs(relevant) <= NEAR_PCT)),
        })
    return rows


def _watchlist_section(path) -> dict[str, Any]:
    lists = watchlist.list_watchlists(path=path)
    reached = []
    for entry in lists:
        if not entry["item_count"]: continue
        for item in watchlist.snapshot(entry["id"], path)["rows"]:
            gap = item["target_gap_pct"]
            if gap is None or gap > 0: continue
            reached.append({
                "watchlist_id": entry["id"], "watchlist": entry["name"], "ticker": item["ticker"], "name": item["name"],
                "close": item["close"], "target_price": item["target_price"], "target_gap_pct": gap,
            })
    return {"lists": len(lists), "items": sum(entry["item_count"] for entry in lists), "reached": reached}


def _position_rows(path) -> list[dict[str, Any]]:
    portfolio = snapshot(path)
    covering: dict[str, dict[str, Any]] = {}
    for plan in trading.plan_exposure(path):
        if plan["entered"] and plan["remaining_quantity"] > 0:
            covering.setdefault(plan["ticker"], plan)
    rows = []
    for position in portfolio["positions"]:
        plan = covering.get(position["ticker"])
        close, stop = position["last_close"], float(plan["stop_price"]) if plan else None
        long = plan is None or plan["side"] == "buy"
        rows.append({
            "ticker": position["ticker"], "name": position["name"], "quantity": position["quantity"],
            # 포트폴리오 스냅샷은 비율(0~1)로 주지만 이 응답의 모든 _pct는 퍼센트로 통일한다.
            "avg_cost": position["avg_cost"], "last_close": close,
            "unrealized_pct": position["unrealized_pct"] * 100 if position["unrealized_pct"] is not None else None,
            "weight_pct": (position["weight"] or 0) * 100, "plan_name": plan["name"] if plan else None, "stop_price": stop,
            "stop_distance_pct": ((close - stop) if long else (stop - close)) / close * 100 if stop and close else None,
            "unprotected": plan is None,
        })
    return rows


def build(date: str | None = None, path=None) -> dict[str, Any]:
    """기준일 하루치 브리핑. `date`가 수집된 거래일이면 그날, 아니면 가장 최근 거래일을 본다."""
    days = signals.trading_days(path)
    as_of = date if date in days else (days[-1] if days else None)
    index = days.index(as_of) if as_of else 0
    previous = days[index - 1] if as_of and index > 0 else None

    tracked = tracked_screen_ids(path)
    logs = _screen_logs(as_of, path, tracked)
    plans = _plan_rows(path) if market.active().trading else []
    tickers = {row["ticker"] for log in logs for key in ("entered", "held", "exited") for row in log["diff"][key]}
    plan_tickers = sorted({row["ticker"] for row in plans})
    with db_session(path) as db:
        bars = _recent_bars(db, sorted(tickers), as_of or FAR_FUTURE)
        names = {row["ticker"]: row["name"] for row in db.execute(
            f"SELECT ticker,name FROM instruments WHERE ticker IN ({','.join('?' * len(plan_tickers))})", plan_tickers).fetchall()} if plan_tickers else {}
    for plan in plans:
        plan["ticker_name"] = names.get(plan["ticker"], plan["ticker"])

    screens = []
    for log in logs:
        changes, streak = log["diff"], log["streaks"]
        held = [_signal_row(row, bars, streak) for row in changes["held"]]
        # 수집일이 하나뿐이면 비교 대상이 없다. 이 경우의 entered는 '신규 진입'이 아니라 첫 스냅샷이다.
        screens.append({
            "screen_id": log["screen_id"], "name": log["name"], "date": log["date"], "previous": changes["previous"],
            "baseline": changes["previous"] is None,
            "matched": log["matched"], "stale": bool(as_of and log["date"] < as_of),
            "entered": [_signal_row(row, bars, streak) for row in changes["entered"]],
            "exited": [_signal_row(row, bars, streak) for row in changes["exited"]],
            "held": len(held),
            "streak_leaders": sorted(held, key=lambda row: (-(row["streak_days"] or 0), row["rank"]))[:STREAK_LEADERS],
        })

    exposure = risk.heat(path)
    latest_capture = max((log["date"] for log in logs), default=None)
    warnings = list(exposure["warnings"])
    if tracked is not None and not tracked:
        warnings.append("브리핑에서 추적할 프리셋이 없습니다 — 브리핑 설정에서 프리셋을 선택하세요")
    elif not logs:
        warnings.append("신호 로그가 비어 있습니다 — `mscr signals capture`로 프리셋 신호를 먼저 수집하세요")
    elif as_of and latest_capture < as_of:
        warnings.append(f"최근 신호 수집일이 {latest_capture}로 기준일 {as_of}보다 오래되었습니다 — 신호 수집을 실행하세요")
    return {
        "as_of": as_of, "previous": previous, "tracked_screen_ids": tracked, "screens": screens, "plans": plans,
        "watchlist": _watchlist_section(path), "positions": _position_rows(path),
        "heat": {
            "heat_pct": exposure["heat_pct"], "total_risk_krw": exposure["total_risk_krw"],
            "open_risk_krw": exposure["open_risk_krw"], "pending_risk_krw": exposure["pending_risk_krw"],
            "over_limit": exposure["over_limit"], "remaining_krw": exposure["budget"]["remaining_krw"],
            "limits": exposure["limits"],
        },
        "warnings": list(dict.fromkeys(warnings)),
    }


def _pct(value: float | None, sign: bool = True) -> str:
    if value is None: return "—"
    return f"{value:+.2f}%" if sign else f"{value:.2f}%"


def _price(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _money(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}원"


def render(brief: dict[str, Any]) -> str:
    """CLI용 요약 텍스트. 비어 있는 구획도 건수는 항상 보여 준다(수집 누락과 '오늘 없음'을 구분하기 위해)."""
    header = f"[{brief['as_of'] or '기준일 없음'}] 장 마감 브리핑"
    if brief.get("previous"): header += f" (직전 {brief['previous']})"
    lines = [header]

    screens = brief["screens"]
    entered = sum(len(entry["entered"]) for entry in screens if not entry.get("baseline"))
    exited = sum(len(entry["exited"]) for entry in screens)
    baselines = sum(1 for entry in screens if entry.get("baseline"))
    summary = f"스크리너: 프리셋 {len(screens)}개 · 신규 {entered}건 · 이탈 {exited}건"
    lines.append(summary + (f" · 최초 수집 {baselines}개" if baselines else ""))
    for entry in screens:
        stale = " (수집 지연)" if entry["stale"] else ""
        if entry.get("baseline"):
            lines.append(f"  {entry['name']} {entry['date'] or '-'}{stale} · 매칭 {entry['matched']} · 최초 수집 (비교 대상 없음)")
        else:
            lines.append(f"  {entry['name']} {entry['date'] or '-'}{stale} · 매칭 {entry['matched']} · 신규 {len(entry['entered'])} · 이탈 {len(entry['exited'])} · 유지 {entry['held']}")
        label = "후보" if entry.get("baseline") else "신규"
        for row in entry["entered"][:RENDER_ROWS]:
            lines.append(f"    {label} {row['ticker']} {row['name']} {row['rank']}위 {_price(row['close'])} {_pct(row['change_pct'])}")
        if len(entry["entered"]) > RENDER_ROWS:
            lines.append(f"    …외 {len(entry['entered']) - RENDER_ROWS}건")
        for row in entry["exited"][:RENDER_ROWS]:
            lines.append(f"    이탈 {row['ticker']} {row['name']} {_price(row['close'])} {_pct(row['change_pct'])}")
        if len(entry["exited"]) > RENDER_ROWS:
            lines.append(f"    …외 {len(entry['exited']) - RENDER_ROWS}건")
        for row in entry["streak_leaders"]:
            lines.append(f"    연속 {row['ticker']} {row['name']} {row['streak_days']}일 {_price(row['close'])} {_pct(row['change_pct'])}")

    plans = brief["plans"]
    lines.append(f"계획: 진행 {len(plans)}건 · 임박 {sum(1 for plan in plans if plan['near'])}건")
    for plan in plans:
        if plan["distance_pct"] is not None: gap = f"진입까지 {_pct(plan['distance_pct'])}"
        elif plan["stop_distance_pct"] is not None: gap = f"손절까지 {_pct(plan['stop_distance_pct'])}"
        else: gap = "시세 없음"
        mark = "!" if plan["near"] else "-"
        lines.append(f"  {mark} {plan['name']} {plan['ticker']} {plan['ticker_name']} · {PHASE_LABELS.get(plan['phase'], plan['phase'])} · 현재 {_price(plan['close'])} · {gap} · {plan['reason']}")

    watch = brief["watchlist"]
    lines.append(f"관심종목: 목록 {watch['lists']}개 · 종목 {watch['items']}개 · 목표 도달 {len(watch['reached'])}건")
    for item in watch["reached"]:
        lines.append(f"  {item['watchlist']} {item['ticker']} {item['name']} · 현재 {_price(item['close'])} / 목표 {_price(item['target_price'])} ({_pct(item['target_gap_pct'])})")

    positions = brief["positions"]
    lines.append(f"보유: {len(positions)}종목 · 손절 미설정 {sum(1 for row in positions if row['unprotected'])}종목")
    for row in positions:
        guard = "손절 계획 없음" if row["unprotected"] else f"손절 {_price(row['stop_price'])} ({_pct(row['stop_distance_pct'])}) · {row['plan_name']}"
        lines.append(f"  {row['ticker']} {row['name']} {row['quantity']:g}주 · 평단 {_price(row['avg_cost'])} → {_price(row['last_close'])} ({_pct(row['unrealized_pct'])}) · 비중 {row['weight_pct']:.1f}% · {guard}")

    heat = brief["heat"]
    lines.append(
        f"리스크: 히트 {_pct(heat['heat_pct'], sign=False)} / 한도 {heat['limits']['max_portfolio_heat_pct']:g}%"
        f" · 총 {_money(heat['total_risk_krw'])} (진행 {_money(heat['open_risk_krw'])} · 대기 {_money(heat['pending_risk_krw'])})"
        f" · 여유 {_money(heat['remaining_krw'])}" + (" · 한도 초과" if heat["over_limit"] else ""))

    lines.append(f"경고: {len(brief['warnings'])}건")
    for warning in brief["warnings"]:
        lines.append(f"  - {warning}")
    return "\n".join(lines)
