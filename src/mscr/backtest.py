"""저장된 프리셋 신호 로그를 일봉으로 재현해 진입·청산 규칙의 기대값을 측정한다.

README `프리셋 관리` 절의 측정(진입 = 다음 봉 시가 또는 박스 상단 매수 스톱, 손절 = k·ATR 또는
박스 바닥, 목표 = 리스크의 n배, 보유 60거래일, 한 봉에 손절·목표가 함께 닿으면 손절 우선,
왕복비용 0.25%)을 그대로 옮긴 것이라 같은 프리셋·같은 기간이면 README의 수치가 재현된다.

스크리너를 다시 돌리지 않고 `screen_signals`에 쌓인 신호만 읽으므로 결과는 신호 로그가 덮는
구간에만 해당한다. 비율(`*_rate`)은 0~100 퍼센트, `expectancy_r`·`stderr_r`은 R 단위다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .db import db_session
from .market import bar_source

DEFAULT_PROTOCOL: dict[str, Any] = {
    "entry": "next_open",        # "next_open" | "breakout"
    "trigger_window": 5,         # breakout: 트리거가 걸릴 때까지 허용하는 봉 수
    "trigger_buffer_pct": 0.1,   # breakout: 박스 상단 위 이만큼에 매수 스톱을 둔다
    "stop_mode": "atr",          # "atr" | "box"
    "atr_multiple": 2.0,
    "atr_period": 14,
    "box_lookback": 20,
    "box_buffer_atr": 0.25,
    "target_r": 3.0,
    "horizon_days": 60,
    "cost_pct": 0.25,
    "top_n": 5,                  # 신호일별 상위 rank만 사용, None이면 전부
    "non_overlap": True,         # 같은 종목의 직전 거래가 열려 있는 동안의 신호는 무시
}
ENTRY_MODES = ("next_open", "breakout")
STOP_MODES = ("atr", "box")
MIN_TRADES = 30
MIN_MONTHS = 6
TICKER_CHUNK = 400
JUMP_RATIO = 1.3  # KRX 일일 가격제한폭. 이보다 큰 종가 변화는 시세가 아니라 감자·액면병합이다.
EXPECTANCY_NOTE = "기대값은 실제로 진입한 거래만 반영합니다. 트리거되지 않은 신호는 분모에서 빠집니다."


def _count(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}은(는) 1 이상의 정수여야 합니다") from None
    if number < 1: raise ValueError(f"{label}은(는) 1 이상의 정수여야 합니다")
    return number


def _amount(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}은(는) 숫자여야 합니다") from None
    if not number >= 0: raise ValueError(f"{label}은(는) 0 이상이어야 합니다")
    return number


def _protocol(protocol: dict[str, Any] | None) -> dict[str, Any]:
    """기본 프로토콜에 사용자 설정을 덮어쓴다. 모르는 키는 오타일 가능성이 커서 거부한다."""
    rule = dict(DEFAULT_PROTOCOL)
    for key, value in (protocol or {}).items():
        if key not in rule: raise ValueError(f"알 수 없는 프로토콜 항목입니다: {key}")
        rule[key] = value
    if rule["entry"] not in ENTRY_MODES: raise ValueError("진입 방식은 next_open 또는 breakout이어야 합니다")
    if rule["stop_mode"] not in STOP_MODES: raise ValueError("손절 방식은 atr 또는 box여야 합니다")
    for key in ("trigger_window", "atr_period", "box_lookback", "horizon_days"):
        rule[key] = _count(rule[key], key)
    for key in ("trigger_buffer_pct", "atr_multiple", "box_buffer_atr", "target_r", "cost_pct"):
        rule[key] = _amount(rule[key], key)
    if rule["target_r"] <= 0: raise ValueError("target_r은 0보다 커야 합니다")
    if rule["stop_mode"] == "atr" and rule["atr_multiple"] <= 0: raise ValueError("atr_multiple은 0보다 커야 합니다")
    rule["top_n"] = None if rule["top_n"] in (None, "") else _count(rule["top_n"], "top_n")
    rule["non_overlap"] = bool(rule["non_overlap"])
    return rule


def _screen_name(db, screen_id: int) -> str:
    row = db.execute("SELECT name FROM screens WHERE id=?", (int(screen_id),)).fetchone()
    if row is None: raise ValueError("프리셋을 찾을 수 없습니다")
    return row["name"]


def _signal_rows(db, screen_id: int, top_n: int | None) -> list[tuple[str, str, int]]:
    query = "SELECT date,ticker,rank FROM screen_signals WHERE screen_id=?"
    params: list[Any] = [int(screen_id)]
    if top_n:
        query += " AND rank<=?"
        params.append(int(top_n))
    return [(row[0], row[1], row[2]) for row in db.execute(query + " ORDER BY date,rank", params).fetchall()]


EMPTY_BARS: dict[str, Any] = {"index": {}, "date": [], "open": [], "high": [], "low": [], "close": [], "atr": [], "box_top": [], "box_bottom": [], "end": []}


def _load_bars(db, tickers: set[str], rule: dict[str, Any] | None = None) -> dict[str, Any]:
    """필요한 종목의 일봉을 한 번에 읽어 ATR·박스 상단/하단까지 미리 계산한다.

    시뮬레이션은 봉을 하나씩 훑어야 하는데 numpy 스칼라 접근이 파이썬 리스트보다 몇 배 느려서
    계산이 끝난 열은 전부 리스트로 눕히고, 종목 경계는 행마다 미리 구한 블록 끝(`end`)으로 판정한다.
    `rule`이 없으면 지표 없이 가격만 채운다.
    """
    codes = sorted(tickers)
    rows: list[tuple] = []
    for start in range(0, len(codes), TICKER_CHUNK):
        chunk = codes[start:start + TICKER_CHUNK]
        marks = ",".join("?" * len(chunk))
        rows.extend(tuple(row) for row in db.execute(
            f"SELECT ticker,date,open,high,low,close,halted FROM daily_bars WHERE source='{bar_source()}' AND ticker IN ({marks})", chunk).fetchall())
    if not rows: return dict(EMPTY_BARS)
    frame = pd.DataFrame(rows, columns=["ticker", "date", "open", "high", "low", "close", "halted"])
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    prices = frame[["open", "high", "low", "close"]]
    # 거래정지일은 체결이 불가능하고 시가·고가·저가가 0으로 들어오기도 한다. 스크리너(dynamic)와 같이 통째로 뺀다.
    frame = frame[np.isfinite(prices).all(axis=1) & (prices > 0).all(axis=1) & (frame["high"] >= frame["low"]) & (frame["halted"].fillna(0) == 0)]
    if frame.empty: return dict(EMPTY_BARS)
    frame = frame.sort_values(["ticker", "date"], kind="stable").reset_index(drop=True)
    # 감자·액면병합으로 미수정 종가가 하루 만에 ±30%를 넘게 뛴 지점은 종목이 갈린 것처럼 다룬다
    # (dynamic.truncate_price_jump와 같은 임계). 지표가 단절 이전 가격에 오염되지 않고, 보유 중이던
    # 거래도 단절을 넘어가지 않는다 — 넘기면 액면병합 하락이 손절로 잡혀 손익이 통째로 거짓이 된다.
    ratio = frame["close"] / frame.groupby("ticker", sort=False)["close"].shift(1)
    frame["segment"] = ((ratio > JUMP_RATIO) | (ratio < 1 / JUMP_RATIO)).astype(int).groupby(frame["ticker"], sort=False).cumsum()
    grouped = frame.groupby(["ticker", "segment"], sort=False)
    bars = {
        "index": {key: position for position, key in enumerate(zip(frame["ticker"], frame["date"]))},
        "date": frame["date"].tolist(),
        "open": frame["open"].tolist(), "high": frame["high"].tolist(),
        "low": frame["low"].tolist(), "close": frame["close"].tolist(),
        "end": (np.arange(len(frame)) + grouped.cumcount(ascending=False).to_numpy() + 1).tolist(),
    }
    if rule is None:
        return bars | {"atr": [], "box_top": [], "box_bottom": []}
    period, lookback = rule["atr_period"], rule["box_lookback"]
    previous = grouped["close"].shift(1)
    span = frame["high"] - frame["low"]
    # 직전 종가가 없는 첫 봉은 갭을 쓸 수 없어 당일 고저 범위만 본다(indicators.atr과 같은 규약).
    frame["tr"] = pd.concat([span, (frame["high"] - previous).abs(), (frame["low"] - previous).abs()], axis=1).max(axis=1).where(previous.notna(), span)
    order = grouped.cumcount()
    seed = frame.groupby(["ticker", "segment"], sort=False)["tr"].transform(lambda values: values.rolling(period, min_periods=period).mean())
    # Wilder 평활: 앞 period-1봉은 미확정, period번째 봉의 단순평균을 씨앗으로 alpha=1/period 재귀.
    seeded = frame["tr"].where(order >= period, np.nan).where(order != period - 1, seed)
    atr = seeded.groupby([frame["ticker"], frame["segment"]], sort=False).transform(lambda values: values.ewm(alpha=1 / period, adjust=False).mean())
    return bars | {
        "atr": atr.tolist(),
        "box_top": grouped["high"].transform(lambda values: values.rolling(lookback, min_periods=1).max()).tolist(),
        "box_bottom": grouped["low"].transform(lambda values: values.rolling(lookback, min_periods=1).min()).tolist(),
    }


def _entry(bars: dict[str, Any], position: int, end: int, rule: dict[str, Any]) -> tuple[str, int, float]:
    """진입 봉 위치와 체결가를 정한다.

    `pending`은 신호일 뒤 봉이 모자라 아직 판정할 수 없다는 뜻이고(통계에서 통째로 제외),
    `untriggered`는 트리거창을 다 보고도 돌파하지 못했다는 뜻이다(신호로는 세고 거래로는 안 센다).
    """
    first = position + 1
    if first >= end: return "pending", 0, 0.0
    if rule["entry"] == "next_open":
        return "entered", first, bars["open"][first]
    top = bars["box_top"][position]
    if not top > 0: return "pending", 0, 0.0
    trigger = top * (1 + rule["trigger_buffer_pct"] / 100)
    highs, opens = bars["high"], bars["open"]
    last = first + rule["trigger_window"]
    for step in range(first, min(last, end)):
        # 갭 상승이면 트리거가 아니라 시가에 체결된다.
        if highs[step] >= trigger: return "entered", step, max(opens[step], trigger)
    return ("untriggered" if last <= end else "pending"), 0, 0.0


def _exit(bars: dict[str, Any], entry_position: int, end: int, entry_price: float, stop: float, target: float, horizon: int) -> tuple[int, float, str]:
    opens, highs, lows, closes = bars["open"], bars["high"], bars["low"], bars["close"]
    last = min(entry_position + horizon, end - 1)
    # 진입 봉은 이미 시가/트리거에 들어갔으므로 갭 판정 없이 장중 터치만 본다.
    if lows[entry_position] <= stop: return entry_position, stop, "stop"
    if highs[entry_position] >= target: return entry_position, target, "target"
    for step in range(entry_position + 1, last + 1):
        opened = opens[step]
        if opened <= stop: return step, opened, "stop"        # 레벨을 뚫고 시작한 갭은 시가 체결
        if opened >= target: return step, opened, "target"
        if lows[step] <= stop: return step, stop, "stop"      # 한 봉에 둘 다 닿으면 손절 우선
        if highs[step] >= target: return step, target, "target"
    return last, closes[last], "timeout"


def _stop_price(bars: dict[str, Any], position: int, entry_price: float, rule: dict[str, Any]) -> float:
    atr = bars["atr"][position]
    if rule["stop_mode"] == "atr":
        return entry_price - rule["atr_multiple"] * atr
    # 완충이 0이면 ATR이 미확정인 초기 구간에서도 박스 바닥만으로 손절을 잡을 수 있다.
    buffer = rule["box_buffer_atr"] * atr if rule["box_buffer_atr"] else 0.0
    return bars["box_bottom"][position] - buffer


def _simulate(rows: list[tuple[str, str, int]], bars: dict[str, Any], rule: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    index, ends = bars["index"], bars["end"]
    horizon, target_r, cost = rule["horizon_days"], rule["target_r"], rule["cost_pct"] / 100
    non_overlap = rule["non_overlap"]
    last_exit: dict[str, int] = {}
    trades: list[dict[str, Any]] = []
    counts = {"signals": 0, "triggered": 0, "missing": 0, "pending": 0, "bad_risk": 0, "overlap": 0, "truncated": 0}
    for date, ticker, _rank in rows:
        position = index.get((ticker, date))
        if position is None:
            counts["missing"] += 1
            continue
        end = ends[position]
        status, entry_position, entry_price = _entry(bars, position, end, rule)
        if status == "pending":
            counts["pending"] += 1
            continue
        # 미체결 신호는 진입일이 없으므로 가장 이른 진입 후보(다음 봉)로 중복 여부를 본다.
        nominal = entry_position if status == "entered" else position + 1
        if non_overlap and nominal <= last_exit.get(ticker, -1):
            counts["overlap"] += 1
            continue
        counts["signals"] += 1
        if status != "entered": continue
        counts["triggered"] += 1
        risk = entry_price - _stop_price(bars, position, entry_price, rule)
        if not risk > 0:  # ATR 미확정(NaN)도 여기서 걸러진다
            counts["bad_risk"] += 1
            continue
        stop, target = entry_price - risk, entry_price + target_r * risk
        exit_position, exit_price, reason = _exit(bars, entry_position, end, entry_price, stop, target, horizon)
        last_exit[ticker] = exit_position
        if reason == "timeout" and entry_position + horizon > end - 1: counts["truncated"] += 1
        trades.append({
            "date": date, "ticker": ticker, "entry_date": bars["date"][entry_position], "exit_date": bars["date"][exit_position],
            "reason": reason, "days": exit_position - entry_position, "risk_pct": risk / entry_price * 100,
            "r": (exit_price - entry_price) / risk - cost * entry_price / risk,
        })
    return trades, counts


def _mean(values: list[float], digits: int = 4) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


def _stderr(values: list[float]) -> float | None:
    if len(values) < 2: return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return round((variance / len(values)) ** 0.5, 4)


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def _by_month(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = {}
    for trade in trades:
        buckets.setdefault(trade["entry_date"][:7], []).append(trade["r"])
    return [{"month": month, "trades": len(values), "expectancy_r": _mean(values)} for month, values in sorted(buckets.items())]


def _by_half(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """신호일 기준으로 기간을 반으로 갈라 각 반기를 따로 본다. README가 쓰는 정직성 점검이다."""
    dates = sorted({trade["date"] for trade in trades})
    if not dates: return []
    middle = dates[len(dates) // 2]
    first = [trade["r"] for trade in trades if trade["date"] < middle]
    second = [trade["r"] for trade in trades if trade["date"] >= middle]
    if not first:  # 신호일이 하나뿐이면 가를 수 없다
        return [{"label": f"전체 ({dates[0]}~{dates[-1]})", "trades": len(second), "expectancy_r": _mean(second)}]
    return [
        {"label": f"전반 ({dates[0]}~{max(trade['date'] for trade in trades if trade['date'] < middle)})", "trades": len(first), "expectancy_r": _mean(first)},
        {"label": f"후반 ({middle}~{dates[-1]})", "trades": len(second), "expectancy_r": _mean(second)},
    ]


def _warnings(counts: dict[str, int], trades: int, months: int, rule: dict[str, Any]) -> list[str]:
    notes = [EXPECTANCY_NOTE]
    if trades < MIN_TRADES: notes.append(f"표본이 {trades}건으로 {MIN_TRADES}건 미만입니다. 소수점 자리는 신뢰하지 마세요.")
    if months < MIN_MONTHS: notes.append(f"신호 기간이 {months}개월뿐이라 특정 국면에 치우쳤을 수 있습니다.")
    if counts["missing"]: notes.append(f"신호일 일봉이 없어 제외한 신호 {counts['missing']}건.")
    if counts["pending"]: notes.append(f"신호일 이후 봉이 모자라 아직 판정할 수 없는 신호 {counts['pending']}건.")
    if counts["bad_risk"]: notes.append(f"손절폭이 0 이하(또는 ATR 미확정)라 제외한 신호 {counts['bad_risk']}건.")
    if counts["overlap"] and rule["non_overlap"]: notes.append(f"직전 거래가 열려 있어 건너뛴 중복 신호 {counts['overlap']}건.")
    if counts["truncated"]: notes.append(f"보유 기간이 끝나기 전에 일봉이 끊겨(수집 종료 또는 감자·액면병합 단절) 마지막 종가로 청산한 거래 {counts['truncated']}건.")
    return notes


def run(screen_id: int, protocol: dict[str, Any] | None = None, path=None) -> dict[str, Any]:
    """프리셋 신호 로그를 주어진 진입·청산 프로토콜로 재현한다.

    `signals`는 중복 제거와 판정 가능 여부를 통과한 신호 수, `triggered`는 그중 진입이 걸린 수,
    `trades`는 손절폭이 유효해 실제로 시뮬레이션된 거래 수다. 기대값은 `trades`만의 평균이다.
    단위: `trigger_rate`·`win_rate`·`target_rate`·`stop_rate`·`timeout_rate`·`avg_risk_pct`는
    0~100 퍼센트이고, `expectancy_r`·`stderr_r`은 R 배수 그대로다(화면에서 다시 100을 곱하지 말 것).
    """
    rule = _protocol(protocol)
    with db_session(path) as db:
        name = _screen_name(db, screen_id)
        rows = _signal_rows(db, screen_id, rule["top_n"])
        bars = _load_bars(db, {row[1] for row in rows}, rule) if rows else dict(EMPTY_BARS)
    dates = sorted({row[0] for row in rows})
    period = {"start": dates[0] if dates else None, "end": dates[-1] if dates else None, "days": len(dates)}
    if not rows:
        return {"screen_id": int(screen_id), "name": name, "protocol": rule, "period": period, "signals": 0, "triggered": 0, "trades": 0,
                "trigger_rate": None, "expectancy_r": None, "stderr_r": None, "win_rate": None, "target_rate": None, "stop_rate": None,
                "timeout_rate": None, "avg_days_held": None, "avg_risk_pct": None, "by_month": [], "by_half": [],
                "profitable_months": 0, "total_months": 0, "warnings": ["신호 로그가 없습니다. 먼저 신호를 수집하세요."]}
    trades, counts = _simulate(rows, bars, rule)
    results = [trade["r"] for trade in trades]
    total = len(trades)
    reasons = {key: sum(1 for trade in trades if trade["reason"] == key) for key in ("target", "stop", "timeout")}
    months = _by_month(trades)
    return {
        "screen_id": int(screen_id), "name": name, "protocol": rule, "period": period,
        "signals": counts["signals"], "triggered": counts["triggered"], "trades": total,
        "trigger_rate": _rate(counts["triggered"], counts["signals"]),
        "expectancy_r": _mean(results), "stderr_r": _stderr(results),
        "win_rate": _rate(sum(1 for value in results if value > 0), total),
        "target_rate": _rate(reasons["target"], total), "stop_rate": _rate(reasons["stop"], total), "timeout_rate": _rate(reasons["timeout"], total),
        "avg_days_held": _mean([trade["days"] for trade in trades], 1),
        "avg_risk_pct": _mean([trade["risk_pct"] for trade in trades], 2),
        "by_month": months, "by_half": _by_half(trades),
        "profitable_months": sum(1 for month in months if (month["expectancy_r"] or 0) > 0), "total_months": len(months),
        "warnings": _warnings(counts, total, len({date[:7] for date in dates}), rule),
    }


def forward_returns(screen_id: int, horizons=(5, 20, 60), top_n: int | None = 5, path=None) -> dict[str, Any]:
    """청산 규칙 없이 신호일 종가에서 h거래일 뒤 종가까지의 단순 수익률 분포.

    "후보를 그냥 들고 있었으면 어땠나"를 보는 기준선이라 비용도 손절도 반영하지 않는다.
    `*_pct`와 `win_rate`는 모두 0~100 퍼센트다.
    """
    steps = sorted({_count(value, "horizons") for value in horizons})
    if not steps: raise ValueError("보유 기간을 하나 이상 지정하세요")
    limit = None if top_n in (None, "") else _count(top_n, "top_n")
    with db_session(path) as db:
        name = _screen_name(db, screen_id)
        rows = _signal_rows(db, screen_id, limit)
        bars = _load_bars(db, {row[1] for row in rows}) if rows else dict(EMPTY_BARS)
    index, ends, closes = bars["index"], bars["end"], bars["close"]
    buckets: dict[int, list[float]] = {step: [] for step in steps}
    signals = missing = 0
    for date, ticker, _rank in rows:
        position = index.get((ticker, date))
        if position is None:
            missing += 1
            continue
        signals += 1
        base, end = closes[position], ends[position]
        for step in steps:
            forward = position + step
            if forward < end: buckets[step].append((closes[forward] / base - 1) * 100)
    horizon_rows = []
    for step in steps:
        values = buckets[step]
        array = np.array(values, dtype="float64") if values else None
        horizon_rows.append({
            "days": step, "count": len(values), "mean_pct": _mean(values, 3),
            "median_pct": round(float(np.median(array)), 3) if array is not None else None,
            "win_rate": _rate(sum(1 for value in values if value > 0), len(values)),
            "p10_pct": round(float(np.percentile(array, 10)), 3) if array is not None else None,
            "p90_pct": round(float(np.percentile(array, 90)), 3) if array is not None else None,
        })
    notes = ["청산 규칙 없이 신호일 종가에 사서 그대로 들고 있었을 때의 수익률입니다. 비용과 손절은 반영하지 않습니다."]
    if not rows: notes.append("신호 로그가 없습니다. 먼저 신호를 수집하세요.")
    if 0 < signals < MIN_TRADES: notes.append(f"표본이 {signals}건으로 {MIN_TRADES}건 미만입니다. 소수점 자리는 신뢰하지 마세요.")
    if missing: notes.append(f"신호일 일봉이 없어 제외한 신호 {missing}건.")
    short = [row["days"] for row in horizon_rows if row["count"] < signals]
    if short: notes.append(f"이후 봉이 모자라 표본이 줄어든 기간: {', '.join(f'{days}일' for days in short)}.")
    return {"screen_id": int(screen_id), "name": name, "signals": signals, "horizons": horizon_rows, "warnings": notes}
