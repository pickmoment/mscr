"""차트를 보지 않고도 설명할 수 있게, 캔들을 구조로 요약한다.

봉 250개를 그대로 주면 토큰만 크고 형태가 읽히지 않는다. 반대로 한 시점 지표만 주면 "언제부터"가
빠진다. 차트 읽기의 정보는 그 사이 — 시간 축의 구획 — 에 있다. 사람이 차트에서 하는 세 가지를
결정론적으로 계산해 JSON 한 덩어리로 낸다.

1. 구간 나누기(`segments`) — 고·저점이 함께 밀려나는 동안으로 경계를 잡고, 그 구간의 등락으로 라벨을 붙인다
2. 수평선 긋기(`levels`)   — 스윙 가격을 ATR 폭으로 묶어 같은 자리를 몇 번 닿았는지 센다
3. 사건 찍기(`events`)     — 거래량 급증·갭·이평 교차·신고가

눈은 정의 없이도 "여기 뭉쳤네"를 잡지만 여기서는 **정의한 것만 읽힌다**. 얼마나 거칠게 읽을지는
`swing_factor`·`min_swing_ratio`가 정한다 — 이 둘이 곧 판독 해상도이고, 구간 경계가 화면과
어긋나면 고칠 곳도 여기다.

이평선은 화면 차트(`/api/bars` 기본 오버레이)와 같은 5·20·60에 장기 추세용 120을 더해 본다.
화면에 없는 선으로 추세를 말하면 사용자가 눈으로 확인할 수 없기 때문이다.

주기에 대해서는 아무것도 가정하지 않는다. 일봉을 주면 일봉 구조를, 5분봉을 주면 5분봉 구조를
읽는다 — 창 길이는 봉 수로만 센다.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .dynamic import truncate_price_jump
from .indicators import compute_indicators, obv_ratio, slope

DEFAULT_WINDOW = 250
WARMUP = 250          # 창 첫 봉부터 250봉 신고가·장기 이평선이 값을 갖도록 앞에 더 읽어 두는 구간
SWING_FACTOR = 3.0    # 스윙 확정에 필요한 되돌림 = ATR14 × 이 배수
# 문턱은 전부 ATR14나 창 폭에 견줘 정한다. "4% 되돌림" 같은 절대 비율을 쓰면 주기를 탄다 —
# 일봉에서 알맞은 값이 5분봉에서는 아무 스윙도 못 잡고 창 전체를 구간 하나로 뭉갠다.
MIN_SWING_RATIO = 0.05  # 스윙은 적어도 창 전체 폭의 이만큼은 되돌려야 한다(ATR이 너무 작을 때의 바닥)
BOX_ATR_MULT = 2.5    # 박스로 쳐 주는 최대 폭 = ATR14의 이 배수
MIN_SEGMENT_BARS = 3  # 이보다 짧은 되돌림은 제 구간을 주지 않고 이웃에 붙인다
MA_PERIODS = (5, 20, 60, 120)
MAX_SWINGS = 8
MAX_LEVELS = 6
MAX_LEVEL_DISTANCE = 0.4  # 현재가에서 이보다 먼 수평선은 지금 얘기에 쓸모가 없다
MAX_EVENTS = 12


def _round(value: Any, digits: int = 4) -> float | None:
    number = float(value) if value is not None else float("nan")
    return round(number, digits) if math.isfinite(number) else None


def _last_finite(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    return float(finite[-1]) if len(finite) else None


# --- 1. 스윙 고·저점 ------------------------------------------------------

def swings(high: np.ndarray, low: np.ndarray, threshold: np.ndarray) -> list[dict[str, Any]]:
    """ATR 배수만큼 되돌릴 때만 극값을 고·저점으로 확정하는 ZigZag.

    확정 전에는 극값을 계속 갱신하므로 잔파동은 구조에 남지 않는다. 마지막 극값은 아직 되돌림을
    보지 못해 미확정(`confirmed=False`)이다 — 지금 진행 중인 파동이라는 뜻이라 구조 판정에는
    쓰되 "확정된 고점"으로 말하면 안 된다.
    """
    n = len(high)
    pivots: list[dict[str, Any]] = []
    if n < 2:
        return pivots
    direction = 0  # 0 미정 / +1 고점 추적(상승 중) / -1 저점 추적(하락 중)
    hi_pos = lo_pos = 0
    for i in range(1, n):
        limit = threshold[i]
        if not math.isfinite(limit) or limit <= 0:
            continue
        if direction >= 0 and high[i] >= high[hi_pos]:
            hi_pos = i
        if direction <= 0 and low[i] <= low[lo_pos]:
            lo_pos = i
        if direction >= 0 and high[hi_pos] - low[i] >= limit:
            if direction == 0 and lo_pos < hi_pos:
                pivots.append({"pos": lo_pos, "kind": "low", "price": float(low[lo_pos]), "confirmed": True})
            pivots.append({"pos": hi_pos, "kind": "high", "price": float(high[hi_pos]), "confirmed": True})
            direction = -1
            # 확정된 고점 이후 구간의 최저점부터 다시 추적한다(전환 시점 i의 저가가 최저라는 보장이 없다).
            lo_pos = hi_pos + int(np.argmin(low[hi_pos:i + 1]))
        elif direction <= 0 and high[i] - low[lo_pos] >= limit:
            if direction == 0 and hi_pos < lo_pos:
                pivots.append({"pos": hi_pos, "kind": "high", "price": float(high[hi_pos]), "confirmed": True})
            pivots.append({"pos": lo_pos, "kind": "low", "price": float(low[lo_pos]), "confirmed": True})
            direction = 1
            hi_pos = lo_pos + int(np.argmax(high[lo_pos:i + 1]))
    tail = hi_pos if direction > 0 else lo_pos if direction < 0 else None
    if tail is not None and (not pivots or tail > pivots[-1]["pos"]):
        kind = "high" if direction > 0 else "low"
        price = float(high[tail] if direction > 0 else low[tail])
        pivots.append({"pos": tail, "kind": kind, "price": price, "confirmed": False})
    return pivots


# --- 2. 구간 ------------------------------------------------------------

def _runs(pivots: list[dict[str, Any]]) -> list[tuple[int, list[dict[str, Any]]]]:
    """고점·저점이 함께 밀려나는 동안을 한 구간으로 본다(Dow).

    상승 구간은 고점이 직전 고점들을 모두 넘고 눌림 저점이 구간 최저점을 깨지 않는 동안 이어진다.
    둘 중 하나라도 실패하면 그 자리에서 구간이 끝나고, 다음 구간은 그 극값에서 시작한다.
    """
    runs: list[tuple[int, list[dict[str, Any]]]] = []
    current = pivots[:2]
    direction = 1 if pivots[1]["kind"] == "high" else -1
    for pivot in pivots[2:]:
        highs = [item["price"] for item in current if item["kind"] == "high"]
        lows = [item["price"] for item in current if item["kind"] == "low"]
        if direction > 0:
            extends = pivot["price"] > max(highs) if pivot["kind"] == "high" else pivot["price"] > min(lows)
        else:
            extends = pivot["price"] < min(lows) if pivot["kind"] == "low" else pivot["price"] < max(highs)
        if extends:
            current = current + [pivot]
        else:
            runs.append((direction, current))
            current = [current[-1], pivot]
            direction = 1 if pivot["kind"] == "high" else -1
    runs.append((direction, current))
    return runs


def _trim(direction: int, run: list[dict[str, Any]]) -> tuple[int, int]:
    """구간의 양끝을 그 구간을 정의한 극값에 맞춘다.

    상승 구간은 저점에서 시작해 고점에서 끝나야 한다. 갱신 실패로 끝난 구간은 꼬리에 눌림 저점이
    남는데, 그대로 두면 "상승 구간인데 등락률이 마이너스"가 나온다 — 라벨과 숫자가 어긋나면
    읽는 쪽은 둘 중 어느 쪽도 못 믿는다.
    """
    head = "low" if direction > 0 else "high"
    tail = "high" if direction > 0 else "low"
    first = next((index for index, item in enumerate(run) if item["kind"] == head), 0)
    last = len(run) - 1 - next((index for index, item in enumerate(reversed(run)) if item["kind"] == tail), 0)
    return (run[first]["pos"], run[last]["pos"]) if last > first else (run[0]["pos"], run[-1]["pos"])


def _segment(kind: str, start: int, end: int, dates: list[str], high: np.ndarray, low: np.ndarray,
             close: np.ndarray, volume: np.ndarray) -> dict[str, Any]:
    span_high, span_low = float(high[start:end + 1].max()), float(low[start:end + 1].min())
    half = max(1, (end - start + 1) // 2)
    early, late = volume[start:start + half].mean(), volume[end - half + 1:end + 1].mean()
    ratio = float(late / early) if early > 0 else float("nan")
    return {
        "from": dates[start], "to": dates[end], "bars": end - start + 1, "kind": kind,
        "change": _round(close[end] / close[start] - 1),
        "high": span_high, "low": span_low,
        "range_pct": _round(span_high / span_low - 1) if span_low > 0 else None,
        "vol_trend": "증가" if ratio >= 1.2 else "감소" if ratio <= 0.8 else "유지" if math.isfinite(ratio) else None,
    }


def _fill(spans: list[dict[str, Any]], end_pos: int) -> list[dict[str, Any]]:
    """구간 사이의 빈 곳(추세를 깬 되돌림)을 제 방향을 가진 구간으로 채워 창을 빈틈없이 덮는다.

    빈 곳을 앞뒤 구간에 붙여 버리면 "상승 구간인데 등락률이 마이너스"가 다시 나온다. 되돌림은
    추세의 일부가 아니라 추세를 끝낸 사건이므로 제 구간을 갖는 편이 맞다.
    """
    filled: list[dict[str, Any]] = []
    cursor = 0
    for span in spans:
        if span["start"] - cursor >= MIN_SEGMENT_BARS:
            filled.append({"start": cursor, "stop": span["start"]})
        elif span["start"] > cursor:
            span["start"] = cursor          # 너무 짧은 되돌림은 제 구간을 주지 않고 뒤 구간에 붙인다
        elif span["start"] < cursor:
            span["start"] = cursor          # 앞 구간이 이미 덮은 자리는 앞 구간에 남긴다
        if span["stop"] > span["start"]:
            filled.append(span)
            cursor = span["stop"]
    if not filled:
        return [{"start": 0, "stop": end_pos}]
    if end_pos - cursor >= MIN_SEGMENT_BARS:
        filled.append({"start": cursor, "stop": end_pos})
    else:
        filled[-1]["stop"] = end_pos
    return filled


def segments(pivots: list[dict[str, Any]], dates: list[str], high: np.ndarray, low: np.ndarray, close: np.ndarray,
             volume: np.ndarray) -> list[dict[str, Any]]:
    """창을 상승·하락·횡보 구간으로 끊는다. 구간은 창 전체를 빈틈없이 덮고 서로 겹치지 않으며,
    라벨은 언제나 그 구간의 등락률과 같은 방향을 가리킨다.

    마지막 극값 이후는 아직 진행 중인 파동이다 — 길면 제 구간으로, 짧으면 앞 구간에 붙여 둔다.
    """
    end_pos = len(close) - 1

    def kind_of(start: int, stop: int) -> str:
        # 되돌림 구간은 제 폭에 견줘 순이동이 작으면 방향이 없다고 본다 — 진행 중인 마지막 파동이
        # 대개 여기 걸린다("고점 찍고 3주째 제자리").
        change = close[stop] / close[start] - 1
        span = float(high[start:stop + 1].max()) / float(low[start:stop + 1].min()) - 1
        if abs(change) < span * 0.4:
            return "횡보"
        return "상승" if change > 0 else "하락"

    if len(pivots) < 3:
        # 스윙이 없으면 끊을 자리도 없다. 창 전체를 한 구간으로 두고 방향만 말한다.
        return [_segment(kind_of(0, end_pos), 0, end_pos, dates, high, low, close, volume)]
    spans = [{"start": head, "stop": tail}
             for head, tail in (_trim(direction, run) for direction, run in _runs(pivots)) if tail > head]
    out = []
    for item in _fill(spans, end_pos):
        # 경계가 확정된 뒤에 라벨을 매긴다. `_runs`가 잡아 준 Dow 방향을 그대로 들고 오면 꼬리를
        # 늘리거나 이웃에 붙이는 과정에서 구간이 달라져 "상승 구간인데 등락률이 마이너스"가 된다.
        out.append(_segment(kind_of(item["start"], item["stop"]), item["start"], item["stop"],
                            dates, high, low, close, volume))
    return out


# --- 3. 수평 레벨 --------------------------------------------------------

def levels(pivots: list[dict[str, Any]], dates: list[str], close: float, tolerance: float,
           max_distance: float = MAX_LEVEL_DISTANCE) -> list[dict[str, Any]]:
    """스윙 가격을 ATR 폭 안에서 묶어 "같은 자리"로 본다. 두 번 이상 닿은 묶음만 남긴다.

    터치 횟수는 스윙 극값 기준이라 실제보다 보수적이다 — 스치고 지나간 봉은 세지 않는다.
    1년에 몇 배가 오른 종목은 옛 극값이 현재가에서 너무 멀어 아무 말도 못 해 주므로 거른다.
    전부 걸러지면 그래도 가장 가까운 둘은 남긴다 — 없다는 말보다 멀다는 말이 낫다.
    """
    clusters: list[list[dict[str, Any]]] = []
    for pivot in sorted(pivots, key=lambda item: item["price"]):
        if clusters and pivot["price"] - clusters[-1][0]["price"] <= tolerance:
            clusters[-1].append(pivot)
        else:
            clusters.append([pivot])
    out = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        price = float(np.mean([item["price"] for item in cluster]))
        out.append({
            "price": round(price, 2), "role": "저항" if price > close else "지지",
            "touches": len(cluster), "last": dates[max(item["pos"] for item in cluster)],
            "distance": _round(price / close - 1),
        })
    out.sort(key=lambda item: (-item["touches"], abs(item["distance"] or 0)))
    near = [item for item in out if abs(item["distance"] or 0) <= max_distance]
    if near:
        return near[:MAX_LEVELS]
    return sorted(out, key=lambda item: abs(item["distance"] or 0))[:2]


# --- 4. 박스 ------------------------------------------------------------

def box(high: np.ndarray, low: np.ndarray, close: np.ndarray, max_width: float, min_bars: int) -> dict[str, Any]:
    """마지막 봉에서 뒤로 넓히면서 폭이 기준을 넘기 직전까지를 박스로 본다.

    `max_width`는 스크리닝에서 쓰던 20일 박스폭과 같은 정의((고점-저점)/종가)다.
    """
    last = float(close[-1])
    start = len(close) - 1
    for candidate in range(len(close) - 1, -1, -1):
        width = (float(high[candidate:].max()) - float(low[candidate:].min())) / last
        if width > max_width:
            break
        start = candidate
    bars = len(close) - start
    top, bottom = float(high[start:].max()), float(low[start:].min())
    return {
        "exists": bars >= min_bars, "bars": bars, "high": top, "low": bottom,
        "width_pct": _round((top - bottom) / last),
        "position_in_box": _round((last - bottom) / (top - bottom)) if top > bottom else None,
    }


# --- 5. 사건 ------------------------------------------------------------

def _top(score: np.ndarray, mask: np.ndarray, count: int) -> list[int]:
    hits = np.flatnonzero(mask & np.isfinite(score))
    return sorted(hits[np.argsort(score[hits])[::-1][:count]].tolist())


def events(dates: list[str], frame: dict[str, np.ndarray], offset: int) -> list[dict[str, Any]]:
    """봉 하나짜리 사건만 모은다 — 구간이 말하는 흐름과 겹치지 않게. `offset`은 창 시작 위치다."""
    out: list[dict[str, Any]] = []
    window = slice(offset, None)
    volume_ratio, change, gap = frame["vol_ratio20"][window], frame["ret1"][window], frame["gap"][window]
    atr_pct = frame["atr14_pct"][window]
    for pos in _top(volume_ratio, volume_ratio >= 3.0, 4):
        out.append({"date": dates[pos], "kind": "거래량급증", "vol_ratio20": _round(volume_ratio[pos], 2),
                    "change": _round(change[pos])})
    limit = np.maximum(0.03, 1.5 * np.nan_to_num(atr_pct, nan=0.02))
    for pos in _top(np.abs(gap), np.abs(gap) >= limit, 3):
        filled = frame["gap_filled"][offset + pos]
        out.append({"date": dates[pos], "kind": "갭상승" if gap[pos] > 0 else "갭하락",
                    "gap_pct": _round(gap[pos]), "filled": bool(filled)})
    shock = np.maximum(0.05, 3 * np.nan_to_num(atr_pct, nan=0.02))
    for pos in _top(np.abs(change), np.abs(change) >= shock, 3):
        out.append({"date": dates[pos], "kind": "급등" if change[pos] > 0 else "급락", "change": _round(change[pos])})
    for key, name in (("golden_cross_5_20", "골든크로스"), ("dead_cross_5_20", "데드크로스"),
                      ("new_high_250", "신고가250"), ("new_low_250", "신저가250")):
        hits = np.flatnonzero(frame[key][window].astype(bool))
        if len(hits):
            out.append({"date": dates[int(hits[-1])], "kind": name, "count": int(len(hits))})
    seen, unique = set(), []
    for item in sorted(out, key=lambda row: row["date"], reverse=True):
        key = (item["date"], item["kind"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique[:MAX_EVENTS]


# --- 조립 ---------------------------------------------------------------

def _trend(indicators: dict[str, pd.Series], length: int) -> dict[str, Any]:
    available = [period for period in MA_PERIODS
                 if math.isfinite(float(indicators[f"ma{period}"].to_numpy(dtype=float)[-1]))]
    if len(available) < 2:
        return {"state": None, "ma_order": None, "since": None}
    stacked = np.vstack([indicators[f"ma{period}"].to_numpy(dtype=float) for period in available])
    ok = np.isfinite(stacked).all(axis=0)
    steps = np.diff(stacked, axis=0)
    states = np.where(ok & (steps < 0).all(axis=0), "정배열",
                      np.where(ok & (steps > 0).all(axis=0), "역배열", "혼조"))
    states[~ok] = ""
    state = str(states[-1])
    since = length - 1
    while since > 0 and states[since - 1] == state:
        since -= 1
    order = ">".join(str(period) for period in sorted(available, key=lambda p: -stacked[available.index(p)][-1]))
    return {"state": state or None, "ma_order": order, "since_index": since}


def read(frame: pd.DataFrame, window: int = DEFAULT_WINDOW, swing_factor: float = SWING_FACTOR,
         min_swing_ratio: float = MIN_SWING_RATIO,
         box_atr_mult: float = BOX_ATR_MULT, box_min_bars: int = 15) -> dict[str, Any]:
    """캔들 프레임(date 오름차순, `daily_bars` 열 그대로)을 구조 요약으로 바꾼다.

    주기를 모른다 — 일봉이든 5분봉이든 봉 수로만 센다. 그래서 `bars`·`신고가250`처럼 이름에 "일"을
    쓰지 않는다. 일봉이면 250봉이 약 52주라는 해석은 부르는 쪽 몫이다.

    `calculate_group`과 같은 전처리를 쓴다 — 거래정지 봉을 빼고, 액면병합 등으로 미수정 종가가
    단절된 지점 이전을 잘라낸다. 안 그러면 이평선과 스윙이 단절 이전 가격대에 끌려간다.
    """
    frame = frame.sort_values("date").reset_index(drop=True)
    valid = frame[~frame["halted"].astype(bool)].copy().reset_index(drop=True)
    valid, price_jump_flag = truncate_price_jump(valid)
    if len(valid) < 20:
        # 왜 모자란지까지 말한다. 액면분할 직후면 종목 탓도 데이터 탓도 아니고 잘라낸 탓이다.
        cause = " — 미수정 종가가 단절된 지점(액면분할·병합) 이후만 남았습니다" if price_jump_flag else ""
        raise ValueError(f"구조를 읽기에 봉이 모자랍니다: 유효 {len(valid)}봉 (최소 20봉){cause}")
    series = {name: pd.Series(valid[name].to_numpy(dtype=float)) for name in ("open", "high", "low", "close", "volume")}
    indicators = compute_indicators(series["open"], series["high"], series["low"], series["close"], series["volume"])
    columns = {key: value.to_numpy(dtype=float) if value.dtype != bool else value.to_numpy()
               for key, value in indicators.items() if isinstance(value, pd.Series)}
    close_all = series["close"].to_numpy()
    previous_close = np.r_[np.nan, close_all[:-1]]
    columns["gap"] = series["open"].to_numpy() / previous_close - 1
    # 갭이 메워졌는지 = 이후 어느 봉이든 직전 종가로 되돌아왔는지.
    low_after = np.minimum.accumulate(series["low"].to_numpy()[::-1])[::-1]
    high_after = np.maximum.accumulate(series["high"].to_numpy()[::-1])[::-1]
    columns["gap_filled"] = np.where(columns["gap"] > 0, low_after <= previous_close, high_after >= previous_close)

    offset = max(0, len(valid) - window)
    dates = valid["date"].astype(str).tolist()[offset:]
    high, low, close, volume = (series[name].to_numpy()[offset:] for name in ("high", "low", "close", "volume"))
    # ATR이 아직 없는 앞쪽 봉을 0으로 채우면 문턱이 무너져 창 머리에만 가짜 스윙이 쏟아진다.
    # 예열 구간을 함께 읽으므로 보통은 비지 않지만, 상장 초기 종목은 실제로 빈다.
    atr14 = pd.Series(columns["atr14"][offset:]).bfill().ffill().fillna(0.0).to_numpy()
    span = float(high.max() - low.min())
    threshold = np.maximum(swing_factor * atr14, span * min_swing_ratio)
    pivots = swings(high, low, threshold)
    last_close = float(close[-1])
    atr_last = float(atr14[-1])
    tolerance = max(atr_last, last_close * 0.005)
    atr_pct = atr_last / last_close
    box_width = max(box_atr_mult * atr_pct, 0.02)
    trend = _trend(indicators, len(valid))
    since_index = trend.pop("since_index", None)
    trend["since"] = valid["date"].astype(str).iloc[since_index] if since_index is not None else None
    trend["slope20_pct_per_day"] = _round(_last_finite(slope(series["close"], 20).to_numpy(dtype=float)), 5)

    return {
        "as_of": dates[-1],
        "window": {"from": dates[0], "to": dates[-1], "bars": len(dates)},
        "bars_available": len(valid),
        "halted_bars": int(frame[(frame["date"].astype(str) >= dates[0])]["halted"].astype(bool).sum()),
        "price_jump_flag": price_jump_flag,
        "position": {
            "close": last_close,
            "from_hi250": _round(_last_finite(columns["dist_hi250"])),
            "from_lo250": _round(_last_finite(columns["dist_lo250"])),
            "atr14_pct": _round(_last_finite(columns["atr14_pct"])),
            "rsi14": _round(_last_finite(columns["rsi14"]), 1),
        },
        "trend": trend,
        "segments": segments(pivots, dates, high, low, close, volume),
        "swings": [{"date": dates[item["pos"]], "kind": item["kind"], "price": item["price"],
                    "confirmed": item["confirmed"]} for item in pivots[-MAX_SWINGS:]],
        "levels": levels(pivots, dates, last_close, tolerance),
        "box": box(high, low, close, box_width, box_min_bars),
        "volume": {
            "ratio20": _round(_last_finite(columns["vol_ratio20"]), 2),
            "obv_ratio20": _round(_last_finite(obv_ratio(series["close"], series["volume"], 20).to_numpy(dtype=float)), 3),
            # 매물대가 현재가 위인지 아래인지 — 거래량가중 20이평이 단순 20이평보다 낮으면 음수다.
            "vwma_spread20": _round(_last_finite(columns["vwma_spread20"]), 4),
        },
        "events": events(dates, columns, offset),
        # 이 판독이 무엇을 "의미 있는 움직임"으로 쳤는지 — 구간이 화면과 어긋날 때 볼 곳이다.
        "resolution": {"swing_factor": swing_factor, "min_swing_ratio": min_swing_ratio,
                       "swing_threshold": _round(float(threshold[-1]) / last_close),
                       "box_width": _round(box_width),
                       "swings_found": len(pivots)},
    }
