import numpy as np
import pandas as pd

from btc_perp_bot.strategy import to_execution_events


def test_5m_signal_is_shifted_to_candle_close():
    index = pd.date_range("2025-01-01", periods=20, freq="5min", tz="UTC")
    close = np.arange(20, dtype=float) + 100
    bars = pd.DataFrame({
        "open": close - 0.1, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": np.ones(20), "signal": np.zeros(20, dtype="int8"),
        "trend_long": np.ones(20, dtype=bool), "trend_short": np.zeros(20, dtype=bool),
    }, index=index)
    bars.iloc[-1, bars.columns.get_loc("signal")] = 1
    events = to_execution_events(bars)
    assert events.index[-1] == index[-1] + pd.Timedelta(minutes=5)
    assert events.iloc[-1]["signal"] == 1
    assert events.columns.tolist() == ["signal", "atr", "exit_long", "exit_short"]

def test_1m_filter_rejects_overextended_breakout():
    from btc_perp_bot.strategy import apply_1m_execution_filter

    index = pd.date_range("2025-01-01", periods=40, freq="min", tz="UTC")
    close = np.arange(40, dtype=float) * 0.1 + 100
    bars_1m = pd.DataFrame({
        "open": close - 0.02, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": np.ones(40),
    }, index=index)
    signals_5m = pd.DataFrame({
        "open": [101.0], "high": [102.0], "low": [100.0], "close": [101.5],
        "volume": [1.0], "signal": [1], "prior_high": [90.0], "prior_low": [89.0],
    }, index=pd.DatetimeIndex([index[15]], name="timestamp"))
    events = pd.DataFrame({
        "signal": [1], "atr": [1.0],
    }, index=pd.DatetimeIndex([index[15] + pd.Timedelta(minutes=5)], name="timestamp"))
    result = apply_1m_execution_filter(events, bars_1m, signals_5m)
    assert result.iloc[0]["signal"] == 0
    assert result.iloc[0]["filter_reason"] == "chase_limit"