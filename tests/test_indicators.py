import numpy as np
import pandas as pd
import pytest

from mscr.indicators import atr, bollinger_bands, crosses, detect_price_jump, ema, historical_volatility, obv, obv_ratio, prior_avg_ratio, rsi, slope, sma


def test_sma_and_ema_conventions():
    np.testing.assert_allclose(sma(pd.Series([1, 2, 3, 4]), 3).to_numpy(), [np.nan, np.nan, 2.0, 3.0], equal_nan=True)
    assert ema(pd.Series([10, 11, 12]), 3).tolist() == [10.0, 10.5, 11.25]


def test_rsi_seed_and_flat_boundaries():
    rising = pd.Series(range(1, 16), dtype=float)
    result = rsi(rising)
    assert result.iloc[:14].isna().all() and result.iloc[14] == 100
    assert pd.isna(rsi(pd.Series([5.0] * 15)).iloc[14])


def test_atr_uses_first_bar_range():
    high = pd.Series([11.0] * 14); low = pd.Series([10.0] * 14); close = pd.Series([10.5] * 14)
    result = atr(high, low, close)
    assert pd.isna(result.iloc[12]) and result.iloc[13] == 1.0


def test_bollinger_population_std_and_hv_sample_std():
    close = pd.Series([float(x) for x in range(1, 21)])
    bb = bollinger_bands(close)
    assert bb["upper"].iloc[-1] == bb["basis"].iloc[-1] + 2 * close.std(ddof=0)
    assert historical_volatility(close, 20).iloc[-1] != close.rolling(20).std(ddof=0).iloc[-1]


def test_prior_avg_ratio_excludes_current_volume():
    assert prior_avg_ratio(pd.Series([10, 10, 10, 10, 10, 50]), 5).iloc[-1] == 5.0


def test_obv_signs_volume_by_close_direction_and_ignores_flat_bars():
    close = pd.Series([10.0, 11.0, 10.0, 10.0, 12.0])
    volume = pd.Series([100.0, 200.0, 300.0, 400.0, 500.0])
    # 첫 봉은 직전 종가가 없어 빠지고, 보합 봉(400)은 0으로 들어간다: 200 - 300 + 0 + 500
    assert obv(close, volume, 4).iloc[-1] == 400.0
    assert pd.isna(obv(close, volume, 5).iloc[-1])


def test_obv_ratio_normalizes_by_the_same_bars_as_the_numerator():
    close = pd.Series([10.0, 11.0, 12.0, 11.0])
    volume = pd.Series([999.0, 100.0, 100.0, 100.0])
    # 분모는 첫 봉의 999를 제외한 300이어야 한다: (100 + 100 - 100) / 300
    assert obv_ratio(close, volume, 3).iloc[-1] == pytest.approx(1 / 3)


def test_obv_ratio_bounds_are_reached_by_one_sided_windows():
    rising = pd.Series([10.0, 11.0, 12.0, 13.0])
    falling = pd.Series([13.0, 12.0, 11.0, 10.0])
    volume = pd.Series([100.0] * 4)
    assert obv_ratio(rising, volume, 3).iloc[-1] == 1.0
    assert obv_ratio(falling, volume, 3).iloc[-1] == -1.0


def test_obv_ratio_is_undefined_when_the_window_has_no_volume():
    close = pd.Series([10.0, 11.0, 12.0, 13.0])
    assert pd.isna(obv_ratio(close, pd.Series([0.0] * 4), 3).iloc[-1])


def test_slope_normalizes_linear_series_by_window_mean():
    assert slope(pd.Series([10.0, 11.0, 12.0, 13.0, 14.0]), 5).iloc[-1] == pytest.approx(1 / 12)


def test_slope_flat_series_is_zero():
    assert slope(pd.Series([5.0] * 5), 5).iloc[-1] == 0.0


def test_cross_requires_prior_equal_boundary():
    golden, _ = crosses(pd.Series([1, 2]), pd.Series([1, 1]))
    assert golden.iloc[-1]


def test_halted_bar_is_removed_by_caller_contract():
    close = pd.Series([float(x) for x in range(1, 21)] + [100.0])
    valid = close.iloc[:-1]
    assert pd.isna(sma(valid, 20).iloc[-1]) is False
    assert len(valid) == 20


def test_price_jump_flag_rule():
    close = pd.Series([100000.0, 20000.0])
    reported = pd.Series([0.0, -1.5])
    assert detect_price_jump(close, reported).iloc[-1]


def _rsi_reference(close: pd.Series, n: int = 14) -> pd.Series:
    values = close.astype(float)
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    valid = values.dropna()
    if len(valid) < n + 1:
        return out
    gains = valid.diff().clip(lower=0)
    losses = (-valid.diff()).clip(lower=0)
    for pos in range(n, len(valid)):
        if pos == n:
            avg_gain = gains.iloc[1:n + 1].mean(); avg_loss = losses.iloc[1:n + 1].mean()
        else:
            avg_gain = (avg_gain * (n - 1) + gains.iloc[pos]) / n
            avg_loss = (avg_loss * (n - 1) + losses.iloc[pos]) / n
        if avg_loss == 0 and avg_gain > 0: value = 100.0
        elif avg_gain == 0 and avg_loss > 0: value = 0.0
        elif avg_gain == 0 and avg_loss == 0: value = np.nan
        else: value = 100 - 100 / (1 + avg_gain / avg_loss)
        out.loc[valid.index[pos]] = value
    return out


def _ema_reference(close: pd.Series, n: int) -> pd.Series:
    values = close.astype(float)
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    previous = None
    for idx, value in values.items():
        if pd.isna(value):
            previous = None
            continue
        previous = float(value) if previous is None else (2 / (n + 1)) * float(value) + (1 - 2 / (n + 1)) * previous
        out.loc[idx] = previous
    return out


def test_vectorized_rsi_and_ema_match_loop_reference():
    # 벡터화 구현은 원래 루프 구현과 값이 같아야 한다 (NaN 공백 포함).
    rng = np.random.default_rng(7)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300))))
    close.iloc[[5, 6, 120, 121, 122, 250]] = np.nan  # 중간 공백
    np.testing.assert_allclose(rsi(close, 14).to_numpy(), _rsi_reference(close, 14).to_numpy(), equal_nan=True, rtol=1e-9)
    np.testing.assert_allclose(ema(close, 20).to_numpy(), _ema_reference(close, 20).to_numpy(), equal_nan=True, rtol=1e-9)
    flat = pd.Series([5.0] * 40)  # 무변화 구간의 경계 규칙(NaN) 유지
    np.testing.assert_allclose(rsi(flat, 14).to_numpy(), _rsi_reference(flat, 14).to_numpy(), equal_nan=True)
