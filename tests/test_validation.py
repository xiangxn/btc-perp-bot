import numpy as np
import pandas as pd
import pytest

from btc_perp_bot.backtest import BacktestConfig
from btc_perp_bot.validation import chronological_splits, compare_exit_policies


def bars(count=30):
    index = pd.date_range("2025-01-01", periods=count, freq="min", tz="UTC")
    close = 100 + np.arange(count, dtype=float) * 0.05
    return pd.DataFrame({
        "open": close, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": np.ones(count),
    }, index=index)


def test_chronological_splits_are_ordered_and_non_overlapping():
    frame = bars(30)
    result = chronological_splits(frame, train_fraction=0.6, validation_fraction=0.2)
    assert len(result["train"]) == 18
    assert len(result["validation"]) == 6
    assert len(result["test"]) == 6
    assert result["train"].index[-1] < result["validation"].index[0]
    assert result["validation"].index[-1] < result["test"].index[0]


def test_invalid_split_fractions_rejected():
    with pytest.raises(ValueError):
        chronological_splits(bars(), train_fraction=0.8, validation_fraction=0.3)


def test_exit_comparison_reports_both_variants():
    frame = bars(30)
    events = pd.DataFrame({
        "signal": np.zeros(30, dtype="int8"), "atr": np.ones(30),
        "exit_long": np.zeros(30, dtype=bool), "exit_short": np.zeros(30, dtype=bool),
    }, index=frame.index)
    result = compare_exit_policies(frame, events, BacktestConfig())
    assert set(result.index) == {"fixed_2R", "trend_exit"}
    assert (result["trade_count"] == 0).all()