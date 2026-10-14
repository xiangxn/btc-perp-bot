import numpy as np
import pandas as pd

from btc_perp_bot.backtest import BacktestConfig, run_backtest


def make_bars():
    index = pd.date_range("2025-01-01", periods=8, freq="min", tz="UTC")
    opens = [100, 100, 100, 102, 103, 104, 105, 106]
    closes = [100, 100, 102, 103, 104, 105, 106, 107]
    highs = [100.5, 100.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.5]
    lows = [99.5, 99.5, 99.5, 101.5, 102.5, 103.5, 104.5, 105.5]
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": np.ones(len(index)),
    }, index=index)


def test_entry_occurs_after_signal_timestamp_and_costs_are_charged():
    bars = make_bars()
    signals = pd.DataFrame({"signal": [1], "atr": [2.0]}, index=[bars.index[1]])
    result = run_backtest(bars, signals, BacktestConfig(
        initial_equity=300, risk_fraction=0.005, fee_rate=0.001,
        slippage_bps=0, stop_atr_multiple=1, target_r_multiple=2,
    ))
    trades = result["trades"]
    assert len(trades) == 1
    assert trades.iloc[0]["entry_time"] == bars.index[2]
    assert trades.iloc[0]["entry_price"] == 100
    assert trades.iloc[0]["entry_fee"] > 0
    assert trades.iloc[0]["net_pnl"] < trades.iloc[0]["gross_pnl"]


def test_no_signal_means_no_trades():
    bars = make_bars()
    signals = pd.DataFrame({"signal": [0] * 3, "atr": [1.0] * 3}, index=bars.index[:3])
    result = run_backtest(bars, signals)
    assert result["summary"]["trade_count"] == 0
    assert result["summary"]["final_equity"] == 300