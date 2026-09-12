"""제약(최대 투자금액·최대 손실 금액)에서 트레이딩 계획을 역산한다.

사용자가 종목과 진입가를 지정하면 남는 자유도는 손절폭 하나뿐이다. 손절폭을 좁히면
같은 손실 한도로 수량이 늘고 목표가도 가까워지지만 손절 확률이 올라간다. 이 모듈은
그 트레이드오프를 후보 표로 펼쳐 보여주고, 각 후보의 목표 도달 확률과 "아무 근거 없이
진입했을 때의 기준선 기대값"을 함께 낸다. 기준선은 예측이 아니며, 사용자의 진입 판단이
그 기준선을 얼마나 넘어야 본전인지를 알려주는 용도다.

목표 도달 통계는 ATR 단위로 정규화되므로 종목·진입가와 무관하다. 따라서 전 종목을
풀링한 무조건 excursion 분포를 한 번 계산해 손절폭 배수별 확률만 캐시해 재사용한다.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .db import db_session
from .market import bar_source
from .indicators import atr

STOP_MULTIPLES = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)
TARGET_MULTIPLES = (2.0, 4.0)
SPLIT = (0.4, 0.3)
HORIZON_DAYS = 60
SAMPLE_CAP = 60_000
MIN_QUANTITY = 3
ATR_PERIOD = 14
LOSS_BUDGET_FLOOR = 0.9
UPSIDE_FLOOR = 1 / 3

_STATS_CACHE: dict[tuple[str, int], dict[float, dict[str, float]]] = {}


def _bars(db) -> pd.DataFrame:
    rows = db.execute(f"SELECT ticker,date,open,high,low,close,halted FROM daily_bars WHERE source='{bar_source()}' ORDER BY ticker,date").fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def _excursion_stats(db, latest: str, horizon: int) -> dict[float, dict[str, float]]:
    """손절폭 배수별 경로 통계. 진입 = 신호 없는 무작위 시점의 다음 봉 시가.

    반환값의 각 항목은 ATR 단위 손절폭 k에 대해:
      reach1/reach2 - 손절과 무관하게 목표에 도달할 확률
      p1/p2         - 손절보다 먼저 목표에 도달할 확률
      stop          - 손절 도달 확률
      b1/b2         - 목표 도달 후 잔량이 손절당할 확률
    """
    cached = _STATS_CACHE.get((latest, horizon))
    if cached is not None: return cached

    frame = _bars(db)
    if frame.empty: raise ValueError("일봉 데이터가 없습니다")
    frame = frame.reset_index(drop=True)
    # 종목 ATR과 반드시 같은 정의를 써야 한다. k 배수가 두 가지를 의미하면 확률이 거리와 어긋난다.
    frame["atr"] = frame.groupby("ticker", sort=False, group_keys=False).apply(
        lambda group: atr(group.high, group.low, group.close, ATR_PERIOD), include_groups=False)

    # 종목 경계를 넘지 않는 관측만 사용한다.
    bounds = np.zeros(len(frame), dtype=np.int64)
    grouped = frame.groupby("ticker", sort=False)
    for start, end in zip(grouped.head(1).index.to_numpy(), grouped.tail(1).index.to_numpy() + 1):
        bounds[start:end] = end
    high, low, open_ = frame.high.to_numpy(), frame.low.to_numpy(), frame.open.to_numpy()
    unit = frame.atr.to_numpy()
    usable = np.flatnonzero((unit > 0) & np.isfinite(unit) & (frame.halted.to_numpy() == 0))
    usable = usable[usable + 1 + horizon <= bounds[usable]]
    if len(usable) < 1000: raise ValueError("목표 도달 통계를 낼 만큼 일봉이 쌓이지 않았습니다")
    if len(usable) > SAMPLE_CAP:
        usable = np.sort(np.random.default_rng(0).choice(usable, size=SAMPLE_CAP, replace=False))

    entry = open_[usable + 1]
    unit = unit[usable]
    window = usable[:, None] + 1 + np.arange(horizon)[None, :]
    up = (np.maximum.accumulate(high[window], axis=1) - entry[:, None]) / unit[:, None]
    down = (entry[:, None] - np.minimum.accumulate(low[window], axis=1)) / unit[:, None]
    keep = (entry > 0) & np.isfinite(up).all(axis=1) & np.isfinite(down).all(axis=1)
    up, down = up[keep], down[keep]
    if not len(up): raise ValueError("목표 도달 통계를 낼 만큼 일봉이 쌓이지 않았습니다")

    def first_touch(cumulative: np.ndarray, level: float) -> np.ndarray:
        """누적 최대/최소는 단조이므로 argmax가 첫 도달 봉이다. 미도달은 horizon."""
        reached = cumulative[:, -1] >= level
        return np.where(reached, (cumulative >= level).argmax(axis=1), horizon)

    m1, m2 = TARGET_MULTIPLES
    stats: dict[float, dict[str, float]] = {}
    for k in STOP_MULTIPLES:
        # 같은 봉에서 손절가와 목표가를 동시에 터치하면 순서를 알 수 없으므로 손절을 먼저 본다.
        t_stop, t1, t2 = first_touch(down, k), first_touch(up, m1 * k), first_touch(up, m2 * k)
        hit1, hit2, stopped = t1 < t_stop, t2 < t_stop, t_stop < horizon
        stats[k] = {
            "reach1": float((up[:, -1] >= m1 * k).mean()),
            "reach2": float((up[:, -1] >= m2 * k).mean()),
            "p1": float(hit1.mean()), "p2": float(hit2.mean()), "stop": float(stopped.mean()),
            "b1": float((hit1 & stopped).mean()), "b2": float((hit2 & stopped).mean()),
            "observations": int(len(up)),
        }
    _STATS_CACHE[(latest, horizon)] = stats
    return stats


def _expectancy(stat: dict[str, float], w1: float, w2: float) -> float:
    """분할 비율 w1/w2에서의 기준선 기대값(R 단위). 미청산 잔량은 0으로 보수 처리."""
    m1, m2 = TARGET_MULTIPLES
    return w1 * m1 * stat["p1"] + w2 * m2 * stat["p2"] - (stat["stop"] - w1 * stat["b1"] - w2 * stat["b2"])


def _breakeven_reach(stat: dict[str, float], w1: float, w2: float) -> float | None:
    """목표 도달 확률이 기준선 대비 같은 배수로 개선된다고 볼 때 기대값 0이 되는 1차 도달률."""
    m1, m2 = TARGET_MULTIPLES
    gain = w1 * m1 * stat["p1"] + w2 * m2 * stat["p2"] + w1 * stat["b1"] + w2 * stat["b2"]
    if gain <= 0: return None
    return min(1.0, stat["stop"] / gain * stat["p1"])


def _latest_atr(db, ticker: str) -> tuple[float, str, float]:
    rows = db.execute(f"SELECT date,high,low,close FROM daily_bars WHERE ticker=? AND source='{bar_source()}' ORDER BY date", (ticker,)).fetchall()
    if len(rows) < ATR_PERIOD + 1: raise ValueError(f"일봉이 {ATR_PERIOD + 1}개 이상 필요합니다 (현재 {len(rows)}개)")
    frame = pd.DataFrame([dict(row) for row in rows])
    series = atr(frame.high, frame.low, frame.close, ATR_PERIOD).dropna()
    if series.empty: raise ValueError("ATR을 계산할 수 없습니다")
    value = float(series.iloc[-1])
    if not value > 0: raise ValueError("ATR이 0입니다")
    return value, str(frame.date.iloc[-1]), float(frame.close.iloc[-1])


def _tick_size(price: float, kind: str) -> float:
    """KRX 호가가격단위 (2023-01-25 개정). ETF·ETN은 개정 대상이 아니라 5원을 유지한다."""
    if kind != "stock": return 5.0
    for ceiling, tick in ((2_000, 1.0), (5_000, 5.0), (20_000, 10.0), (50_000, 50.0), (200_000, 100.0), (500_000, 500.0)):
        if price < ceiling: return tick
    return 1_000.0


def _align(price: float, tick: float, mode: str) -> float:
    steps = price / tick
    if mode == "up": steps = math.ceil(steps - 1e-9)
    elif mode == "down": steps = math.floor(steps + 1e-9)
    else: steps = round(steps)
    return steps * tick


def _shares(budget: float, unit_cost: float) -> int:
    """예산으로 살 수 있는 정수 주식 수. 정확히 나눠지는 경계에서 부동소수점 오차로 1주를 잃지 않게 한다."""
    return max(0, math.floor(budget / unit_cost + 1e-9))


def _candidate(k: float, unit: float, entry: float, long: bool, cap: int, loss_cap: float, tick: float, stat: dict[str, float]) -> dict[str, Any]:
    # 손절가를 먼저 호가 단위로 맞추고, 그 실제 간격으로 수량을 산정한다. 정렬 방향은 진입가 쪽(손절폭 축소)이라
    # 정렬 때문에 최대 손실이 한도를 넘는 일이 없다.
    raw = k * unit
    stop_price = _align(entry - raw, tick, "up") if long else _align(entry + raw, tick, "down")
    distance = abs(entry - stop_price)
    base = {"stop_atr_multiple": k, "stop_distance": distance, "stop_price": stop_price}
    if distance <= 0 or (long and stop_price <= 0):
        return base | {"rejected": f"호가 단위({tick:g}원)에서 손절가가 진입가와 구분되지 않습니다", "quantity": 0}
    quantity = min(cap, _shares(loss_cap, distance))
    if quantity < MIN_QUANTITY:
        return base | {"rejected": f"3분할이 성립하지 않습니다 (가능 수량 {quantity}주, 최소 {MIN_QUANTITY}주)", "quantity": quantity}

    q1, q2 = round(quantity * SPLIT[0]), round(quantity * SPLIT[1])
    q3 = quantity - q1 - q2
    if min(q1, q2, q3) < 1:
        return base | {"rejected": f"3분할이 성립하지 않습니다 (배분 {q1}/{q2}/{q3}주)", "quantity": quantity}

    m1, m2 = TARGET_MULTIPLES
    sign = 1.0 if long else -1.0
    tp1 = _align(entry + sign * m1 * distance, tick, "nearest")
    tp2 = _align(entry + sign * m2 * distance, tick, "nearest")
    ordered = entry < tp1 < tp2 if long else entry > tp1 > tp2
    if not ordered:
        return base | {"rejected": f"호가 단위({tick:g}원)에서 목표가가 서로 겹칩니다", "quantity": quantity}

    w1, w2 = q1 / quantity, q2 / quantity
    return base | {
        "rejected": None,
        "quantity": quantity,
        "invested": quantity * entry,
        "max_loss_krw": quantity * distance,
        "loss_budget_used": quantity * distance / loss_cap,
        "leg_quantities": [q1, q2, q3],
        "tp1_price": tp1, "tp1_ratio": w1,
        "tp2_price": tp2, "tp2_ratio": w2,
        "tp3_trailing_pct": round(unit / entry * 100, 2),
        "reach_tp1_prob": stat["reach1"], "reach_tp2_prob": stat["reach2"],
        "baseline_expectancy_r": round(_expectancy(stat, w1, w2), 3),
        "breakeven_tp1_prob": _breakeven_reach(stat, w1, w2),
    }


def _plan_fields(ticker: str, side: str, entry: float, candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": ticker, "side": side, "quantity": candidate["quantity"],
        "order_type": "limit", "limit_price": entry, "entry_price": entry,
        "stop_price": candidate["stop_price"],
        "tp1_price": candidate["tp1_price"], "tp1_ratio": candidate["tp1_ratio"],
        "tp2_price": candidate["tp2_price"], "tp2_ratio": candidate["tp2_ratio"],
        "tp3_trailing_pct": candidate["tp3_trailing_pct"], "enabled": True,
    }


def propose(ticker: str, side: str, entry_price: float, max_investment: float, max_loss: float, horizon_days: int = HORIZON_DAYS, path=None) -> dict[str, Any]:
    ticker = str(ticker).strip()
    if len(ticker) != 6 or not ticker.isdigit(): raise ValueError("종목코드는 6자리 숫자여야 합니다")
    if side not in ("buy", "sell"): raise ValueError("매매 구분은 buy 또는 sell 이어야 합니다")
    entry = float(entry_price)
    if not entry > 0: raise ValueError("진입가는 0보다 커야 합니다")
    budget, loss_cap = float(max_investment), float(max_loss)
    if not budget > 0: raise ValueError("최대 투자 금액은 0보다 커야 합니다")
    if not loss_cap > 0: raise ValueError("최대 손실 금액은 0보다 커야 합니다")
    if loss_cap > budget: raise ValueError("최대 손실 금액이 최대 투자 금액보다 클 수 없습니다")
    if entry > budget: raise ValueError(f"진입가({entry:,.0f}원)가 최대 투자 금액({budget:,.0f}원)보다 큽니다 — 1주도 살 수 없습니다")

    long = side == "buy"
    with db_session(path) as db:
        row = db.execute("SELECT name,kind FROM instruments WHERE ticker=?", (ticker,)).fetchone()
        if row is None: raise ValueError("등록되지 않은 종목코드입니다")
        name, kind = str(row["name"]), str(row["kind"])
        unit, as_of, reference_close = _latest_atr(db, ticker)
        stats = _excursion_stats(db, as_of, int(horizon_days))

    cap = _shares(budget, entry)
    tick = _tick_size(entry, kind)
    candidates = [_candidate(k, unit, entry, long, cap, loss_cap, tick, stats[k]) for k in STOP_MULTIPLES]
    usable = [index for index, item in enumerate(candidates) if item["rejected"] is None]
    if not usable:
        raise ValueError(f"주어진 제약으로는 계획을 만들 수 없습니다 — 최소 {MIN_QUANTITY}주를 담으려면 최대 손실 금액이 {MIN_QUANTITY * STOP_MULTIPLES[0] * unit:,.0f}원 이상이어야 합니다")

    # 권장 규칙. 예측이 아니라 명시된 규칙이며, 견고한 통계(도달 확률)에만 기댄다.
    # 기준선 기대값은 후보 간 차이가 노이즈 수준이라 순위 기준으로 쓰지 않는다.
    #   (1) 손실 한도를 충분히 소진해야 한다 — 남긴 한도는 그냥 버리는 것이다.
    #   (2) 2차 목표 도달 확률이 실질적이어야 한다 — 트레일링 잔량이 가동되지 않으면 상단이 닫힌다.
    #   (3) 위를 만족하는 중 손절폭이 가장 넓은 것 — 좁은 손절은 종목 자체 변동성에 털린다.
    funded = [index for index in usable if candidates[index]["loss_budget_used"] >= LOSS_BUDGET_FLOOR] or usable
    open_upside = [index for index in funded if candidates[index]["reach_tp2_prob"] >= UPSIDE_FLOOR]
    if open_upside:
        recommended = max(open_upside, key=lambda index: candidates[index]["stop_atr_multiple"])
        reason = (f"손실 한도의 {candidates[recommended]['loss_budget_used'] * 100:.0f}%를 쓰면서 2차 목표 도달 확률이 "
                  f"{candidates[recommended]['reach_tp2_prob'] * 100:.0f}%로 남아 있는 후보 중 손절폭이 가장 넓습니다.")
    else:
        recommended = max(funded, key=lambda index: candidates[index]["reach_tp2_prob"])
        reason = (f"어느 후보도 2차 목표 도달 확률 {UPSIDE_FLOOR * 100:.0f}%를 넘지 못합니다 "
                  f"(최대 {candidates[recommended]['reach_tp2_prob'] * 100:.0f}%). 손실 한도가 이 종목 변동성에 비해 좁습니다.")
    binding = "max_investment" if candidates[recommended]["quantity"] == cap else "max_loss"

    warnings = [
        "최대 손실 금액은 갭 하락 시 보장되지 않습니다. 일봉 EOD 평가 구조라 손절가를 크게 밑도는 시가에 시장가로 청산됩니다.",
        "최대 손실 금액은 이 계획 1건 기준입니다. 다른 계획이 동시에 열려 있으면 합산 손실이 한도를 넘습니다.",
        "기준선 기대값은 진입 근거가 없을 때의 과거 통계이며 예측이 아닙니다. 진입 판단이 이 기준선을 넘어야 이익이 납니다.",
    ]
    chosen = candidates[recommended]
    if binding == "max_loss":
        warnings.insert(0, f"최대 손실 금액이 수량을 결정하고 있습니다. 최대 투자 금액 {budget:,.0f}원은 제약이 되지 않으며 실제 투자액은 {chosen['invested']:,.0f}원입니다.")
    else:
        warnings.insert(0, f"최대 투자 금액이 수량을 결정하고 있습니다. 최대 손실 금액 {loss_cap:,.0f}원 중 {chosen['max_loss_krw']:,.0f}원({chosen['loss_budget_used'] * 100:.0f}%)만 쓰입니다.")
    gap = abs(entry - reference_close) / reference_close
    if gap > 0.1:
        warnings.append(f"진입가 {entry:,.0f}원이 최근 종가 {reference_close:,.0f}원({as_of})과 {gap * 100:.0f}% 떨어져 있습니다. ATR 기준 손절폭이 현재 변동성과 맞지 않을 수 있습니다.")
    if _align(entry, tick, "nearest") != entry:
        warnings.append(f"진입가 {entry:,.0f}원이 이 종목의 호가 단위({tick:g}원)에 맞지 않습니다. 지정가 진입 주문이 접수되지 않을 수 있습니다.")

    return {
        "ticker": ticker, "name": name, "side": side, "entry_price": entry, "as_of": as_of,
        "reference_close": reference_close, "atr": unit, "atr_pct": round(unit / entry * 100, 2), "tick_size": tick,
        "max_investment": budget, "max_loss": loss_cap, "binding": binding,
        "sample": {"observations": stats[STOP_MULTIPLES[0]]["observations"], "horizon_days": int(horizon_days)},
        "candidates": candidates, "recommended": recommended, "recommendation_reason": reason, "warnings": warnings,
        "plan": _plan_fields(ticker, side, entry, candidates[recommended]),
    }
