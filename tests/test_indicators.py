import numpy as np
import pandas as pd

from mscr.indicators import atr, bollinger_bands, crosses, detect_price_jump, ema, historical_volatility, rsi, sma, volume_ratio


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


def test_volume_ratio_excludes_current_volume():
    assert volume_ratio(pd.Series([10, 10, 10, 10, 10, 50]), 5).iloc[-1] == 5.0


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
