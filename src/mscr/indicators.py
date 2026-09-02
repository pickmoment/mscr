from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
import pandas as pd


def _series(values: pd.Series | Iterable[float]) -> pd.Series:
    return values.astype(float) if isinstance(values, pd.Series) else pd.Series(values, dtype="float64")


def _valid(values: pd.Series) -> pd.Series:
    return values.replace([np.inf, -np.inf], np.nan)


def sma(close: pd.Series, n: int) -> pd.Series:
    return _valid(_series(close)).rolling(n, min_periods=n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    # NaN을 만나면 씨앗을 버리고 다음 유효 구간에서 다시 시작한다(구간별 ewm).
    values = _valid(_series(close))
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    mask = values.notna().to_numpy()
    if not mask.any():
        return out
    segment = (~mask).cumsum()
    valid = pd.Series(values.to_numpy(dtype=float)[mask])
    smoothed = valid.groupby(pd.Series(segment[mask])).transform(
        lambda s: s.ewm(alpha=2 / (n + 1), adjust=False).mean())
    result = out.to_numpy()
    result[np.flatnonzero(mask)] = smoothed.to_numpy()
    return pd.Series(result, index=values.index, dtype="float64")


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    values = _valid(_series(close))
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    mask = values.notna().to_numpy()
    valid = values.to_numpy(dtype=float)[mask]
    if len(valid) < n + 1:
        return out
    diff = np.diff(valid)  # 위치 1..m-1의 변화
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    # Wilder 평활: 첫 n개 변화의 단순 평균을 씨앗으로 두고 alpha=1/n 재귀 (atr와 동일한 기법)
    # pd.Series는 numpy 버퍼를 공유할 수 있으므로 씨앗은 접두부를 지우기 전에 계산한다.
    gain_seed, loss_seed = gains[:n].mean(), losses[:n].mean()
    seeded_gain = pd.Series(gains.copy(), dtype="float64")
    seeded_loss = pd.Series(losses.copy(), dtype="float64")
    seeded_gain.iloc[: n - 1] = np.nan
    seeded_loss.iloc[: n - 1] = np.nan
    seeded_gain.iloc[n - 1] = gain_seed
    seeded_loss.iloc[n - 1] = loss_seed
    avg_gain = seeded_gain.ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    avg_loss = seeded_loss.ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        value = 100 - 100 / (1 + avg_gain / avg_loss)
    value = np.where((avg_loss == 0) & (avg_gain > 0), 100.0, value)
    value = np.where((avg_gain == 0) & (avg_loss > 0), 0.0, value)
    value = np.where((avg_gain == 0) & (avg_loss == 0), np.nan, value)
    result = out.to_numpy()
    result[np.flatnonzero(mask)[n:]] = value[n - 1:]
    return pd.Series(result, index=values.index, dtype="float64")


def detect_price_jump(close: pd.Series, reported_pct: pd.Series | None = None) -> pd.Series:
    """Flag split-like transitions when reported change disagrees with raw price."""
    values = close.astype(float)
    raw = values / values.shift(1) - 1
    if reported_pct is None:
        return pd.Series(False, index=values.index)
    reported = reported_pct.astype(float) / 100
    return (raw.abs() > 0.35) & (reported.abs() <= 0.10) & ((raw - reported).abs() > 0.25)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, pd.Series]:
    values = _series(close)
    line = ema(values, fast) - ema(values, slow)
    signal_line = ema(line, signal)
    return {"macd": line, "signal": signal_line, "hist": line - signal_line}


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    h, l, c = _series(high), _series(low), _series(close)
    valid = np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & (h >= l) & (c > 0)
    # 직전 봉이 무효면 갭을 쓸 수 없다. 그 봉은 당일 고저 범위만 본다.
    previous_close = c.shift(1).where(valid.shift(1, fill_value=False))
    span = h - l
    gapped = pd.concat([span, (h - previous_close).abs(), (l - previous_close).abs()], axis=1).max(axis=1)
    return gapped.where(previous_close.notna(), span).where(valid)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    tr = _true_range(high, low, close)
    out = pd.Series(np.nan, index=tr.index, dtype="float64")
    valid = tr.dropna()
    if len(valid) < n:
        return out
    # Wilder 평활: 첫 n개의 단순 평균을 씨앗으로 두고 alpha=1/n 재귀. 무효 봉은 건너뛰고 이어진다.
    seeded = valid.copy()
    seeded.iloc[: n - 1] = np.nan
    seeded.iloc[n - 1] = valid.iloc[:n].mean()
    out.loc[valid.index] = seeded.ewm(alpha=1 / n, adjust=False).mean()
    return out


def bollinger_bands(close: pd.Series, n: int = 20, k: float = 2.0) -> dict[str, pd.Series]:
    values = _series(close)
    basis = values.rolling(n, min_periods=n).mean()
    sigma = values.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = basis + k * sigma, basis - k * sigma
    width = (upper - lower) / basis
    pctb = (values - lower) / (upper - lower)
    return {"basis": basis, "upper": upper, "lower": lower, "pctb": pctb, "width": width}


def returns(close: pd.Series, n: int) -> pd.Series:
    values = _series(close)
    return values / values.shift(n) - 1


def prior_avg_ratio(volume: pd.Series, n: int) -> pd.Series:
    values = _series(volume)
    denominator = values.shift(1).rolling(n, min_periods=n).mean()
    return values.where(denominator > 0) / denominator.where(denominator > 0)


def slope(close: pd.Series, n: int) -> pd.Series:
    values = _series(close)
    x = np.arange(n, dtype=float) - (n - 1) / 2
    denom = (x ** 2).sum()

    def _slope(y: np.ndarray) -> float:
        mean = y.mean()
        return (x * (y - mean)).sum() / denom / mean if mean > 0 else np.nan

    return values.rolling(n, min_periods=n).apply(_slope, raw=True)


def historical_volatility(close: pd.Series, n: int) -> pd.Series:
    values = _series(close)
    log_returns = np.log(values / values.shift(1))
    return log_returns.rolling(n, min_periods=n).std(ddof=1) * math.sqrt(252)


def consecutive(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    values = _series(close)
    up = pd.Series(0, index=values.index, dtype="int64")
    down = pd.Series(0, index=values.index, dtype="int64")
    up_count = down_count = 0
    previous = None
    for idx, value in values.items():
        if pd.isna(value) or previous is None:
            up_count = down_count = 0
        elif value > previous:
            up_count += 1; down_count = 0
        elif value < previous:
            down_count += 1; up_count = 0
        else:
            up_count = down_count = 0
        up.loc[idx], down.loc[idx] = up_count, down_count
        if not pd.isna(value):
            previous = value
    return up, down


def crosses(fast: pd.Series, slow: pd.Series) -> tuple[pd.Series, pd.Series]:
    f, s = _series(fast), _series(slow)
    fa, sa = f.to_numpy(dtype=float), s.to_numpy(dtype=float)
    fp, sp = np.roll(fa, 1), np.roll(sa, 1)
    ok = np.zeros(len(fa), dtype=bool)
    if len(fa) > 1:
        ok[1:] = np.isfinite(fa[1:]) & np.isfinite(sa[1:]) & np.isfinite(fp[1:]) & np.isfinite(sp[1:])
    with np.errstate(invalid="ignore"):
        golden = ok & (fa > sa) & (fp <= sp)
        dead = ok & (fa < sa) & (fp >= sp)
    return pd.Series(golden, index=f.index), pd.Series(dead, index=f.index)


def _valid_ohlc(high: pd.Series, low: pd.Series, close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    h, l, c = _series(high), _series(low), _series(close)
    bad = (~np.isfinite(h)) | (~np.isfinite(l)) | (~np.isfinite(c)) | (c <= 0) | (h < l)
    return h.mask(bad), l.mask(bad), c.mask(bad)


def compute_indicators(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> dict[str, pd.Series | float | int]:
    h, l, c = _valid_ohlc(high, low, close)
    v = _valid(volume)
    m5, m20, m60, m120, m200 = (sma(c, n) for n in (5, 20, 60, 120, 200))
    vm5, vm20, vm60 = (sma(v, n) for n in (5, 20, 60))
    r = {f"ret{n}": returns(c, n) for n in (1, 5, 20, 60, 120, 250)}
    bb = bollinger_bands(c)
    up, down = consecutive(c)
    golden, dead = crosses(m5, m20)
    result: dict[str, pd.Series | float | int] = {
        "ma5": m5, "ma20": m20, "ma60": m60, "ma120": m120, "ma200": m200,
        "vma5": vm5, "vma20": vm20, "vma60": vm60,
        "vol_ratio5": prior_avg_ratio(v, 5), "vol_ratio20": prior_avg_ratio(v, 20),
        **r,
        "dist_ma20": c / m20 - 1, "dist_ma60": c / m60 - 1,
        "hi250": h.rolling(250, min_periods=250).max(), "lo250": l.rolling(250, min_periods=250).min(),
        "rsi14": rsi(c), "atr14": atr(h, l, c),
        "bb20_upper": bb["upper"], "bb20_lower": bb["lower"], "bb20_pctb": bb["pctb"], "bb20_width": bb["width"],
        "hv20": historical_volatility(c, 20), "hv60": historical_volatility(c, 60),
        "consec_up": up, "consec_down": down,
        "ma5_above_ma20": (m5 > m20), "ma20_above_ma60": (m20 > m60),
        "golden_cross_5_20": golden, "dead_cross_5_20": dead,
        "new_high_20": c.eq(c.rolling(20, min_periods=20).max()), "new_high_60": c.eq(c.rolling(60, min_periods=60).max()), "new_high_250": c.eq(c.rolling(250, min_periods=250).max()),
        "new_low_20": c.eq(c.rolling(20, min_periods=20).min()), "new_low_250": c.eq(c.rolling(250, min_periods=250).min()),
    }
    result["dist_hi250"] = c / result["hi250"] - 1  # type: ignore[operator]
    result["dist_lo250"] = c / result["lo250"] - 1  # type: ignore[operator]
    result["atr14_pct"] = result["atr14"] / c  # type: ignore[operator]
    return result
