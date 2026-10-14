import numpy as np
import pandas as pd
import pytest

from btc_perp_bot.data import resample_ohlcv, validate_ohlcv


def bars(count=15):
    index = pd.date_range("2025-01-01", periods=count, freq="min", tz="UTC")
    close = np.arange(count, dtype=float) + 100
    return pd.DataFrame({
        "open": close - 0.25, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": np.ones(count),
    }, index=index)


def test_resample_creates_complete_5m_bars():
    frame = bars(11)
    result = resample_ohlcv(frame, "5m")
    assert len(result) == 2
    assert result.iloc[0]["open"] == frame.iloc[0]["open"]
    assert result.iloc[0]["close"] == frame.iloc[4]["close"]
    assert result.iloc[0]["volume"] == 5


def test_resample_drops_partial_bucket():
    assert len(resample_ohlcv(bars(14), "15m")) == 0


def test_duplicate_timestamp_rejected():
    frame = bars(3)
    frame.index = pd.DatetimeIndex([frame.index[0], frame.index[1], frame.index[1]])
    with pytest.raises(ValueError, match="duplicate"):
        validate_ohlcv(frame)


def test_impossible_ohlc_rejected():
    frame = bars(3)
    frame.loc[frame.index[0], "high"] = 1
    with pytest.raises(ValueError, match="high"):
        validate_ohlcv(frame)