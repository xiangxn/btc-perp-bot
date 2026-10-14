import numpy as np
import pandas as pd

from btc_perp_bot.strategy import to_execution_events


def test_5m_signal_is_shifted_to_candle_close():
    index = pd.date_range("2025-01-01", periods=20, freq="5min", tz="UTC")
    close = np.arange(20, dtype=float) + 100
    bars = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": np.ones(20), "signal": np.zeros(20, dtype="int8"),
    }, index=index)
    bars.iloc[-1, bars.columns.get_loc("signal")] = 1
    events = to_execution_events(bars)
    assert events.index[-1] == index[-1] + pd.Timedelta(minutes=5)
    assert events.iloc[-1]["signal"] == 1
    assert events.columns.tolist() == ["signal", "atr"]