import numpy as np
import pandas as pd
import pytest

from btc_perp_bot.indicators import adx, atr, ema
from btc_perp_bot.strategy import StrategyConfig, generate_signals


def sample_bars(count: int, freq: str = "5min", trend: float = 0.2) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=count, freq=freq, tz="UTC")
    close = 100 + np.arange(count) * trend
    return pd.DataFrame(
        {
            "open": close - 0.05,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.full(count, 1.0),
        },
        index=index,
    )


def test_ema_has_warmup_and_tracks_rising_series():
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ema(values, 3)
    assert result.iloc[:2].isna().all()
    assert result.iloc[-1] > result.iloc[-2]


def test_atr_and_adx_return_nonnegative_values():
    bars = sample_bars(80)
    atr_values = atr(bars, 14)
    adx_values = adx(bars, 14)
    assert (atr_values.dropna() >= 0).all()
    assert ((adx_values.dropna() >= 0) & (adx_values.dropna() <= 100)).all()


def test_invalid_period_rejected():
    with pytest.raises(ValueError):
        ema(pd.Series([1, 2, 3]), 0)


def test_breakout_lookback_excludes_current_candle():
    bars_5m = sample_bars(80, "5min", trend=0.0)
    bars_5m.loc[bars_5m.index[-1], ["open", "close"]] = [110.0, 111.0]
    bars_5m.loc[bars_5m.index[-1], "high"] = 111.2
    bars_15m = sample_bars(80, "15min", trend=0.5)
    config = StrategyConfig(breakout_lookback=12, fast_ema=3, slow_ema=6, adx_period=3, adx_min=0)
    result = generate_signals(bars_5m, bars_15m, config)
    assert result["prior_high"].iloc[-1] == 100.2
    assert result["signal"].iloc[-1] in (0, 1)


def test_invalid_ohlc_is_rejected():
    bars_5m = sample_bars(20)
    bars_5m.loc[bars_5m.index[0], "high"] = 1.0
    bars_15m = sample_bars(20, "15min")
    with pytest.raises(ValueError):
        generate_signals(bars_5m, bars_15m)