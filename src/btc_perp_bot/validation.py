"""Chronological evaluation helpers for out-of-sample strategy research."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from btc_perp_bot.backtest import BacktestConfig, run_backtest


def chronological_splits(
    bars: pd.DataFrame,
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> dict[str, pd.DataFrame]:
    """Split sorted candle data into contiguous train/validation/test periods.

    Splits are chronological and non-overlapping. This helper does not tune parameters or
    purge overlapping trades; callers should treat boundaries as evaluation boundaries and
    inspect forced end-of-segment exits. Feature generation should be causal and may be
    performed before splitting so earlier history can warm up indicators.
    """
    if not 0 < train_fraction < 1:
        raise ValueError("train_fraction must be between 0 and 1")
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between 0 and 1")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train_fraction + validation_fraction must be less than 1")
    if bars.empty or not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("bars must be a non-empty DataFrame with a DatetimeIndex")
    if not bars.index.is_monotonic_increasing or bars.index.has_duplicates:
        raise ValueError("bars must have sorted, unique timestamps")
    n = len(bars)
    train_end = int(n * train_fraction)
    validation_end = int(n * (train_fraction + validation_fraction))
    if train_end < 1 or validation_end <= train_end or validation_end >= n:
        raise ValueError("not enough rows for the requested chronological split")
    return {
        "train": bars.iloc[:train_end].copy(),
        "validation": bars.iloc[train_end:validation_end].copy(),
        "test": bars.iloc[validation_end:].copy(),
    }


def compare_exit_policies(
    bars_1m: pd.DataFrame,
    events: pd.DataFrame,
    config: BacktestConfig = BacktestConfig(),
) -> pd.DataFrame:
    """Compare four stop/exit combinations on the same data and costs.

    This is a descriptive experiment, not an optimizer. It crosses ATR/structural stops with
    fixed 2R/trend-regime exits. The structural stop comes from the prior 12-candle breakout
    range reference carried in the event data. All other configuration values remain unchanged.
    """
    variants = {
        "atr_fixed_2R": replace(config, stop_mode="atr", target_r_multiple=2.0, trend_exit_enabled=False),
        "atr_trend_exit": replace(config, stop_mode="atr", target_r_multiple=None, trend_exit_enabled=True),
        "structural_fixed_2R": replace(config, stop_mode="structural", target_r_multiple=2.0, trend_exit_enabled=False),
        "structural_trend_exit": replace(config, stop_mode="structural", target_r_multiple=None, trend_exit_enabled=True),
    }
    rows: list[dict] = []
    for name, variant_config in variants.items():
        result = run_backtest(bars_1m, events, variant_config)
        rows.append({"variant": name, **result["summary"]})
    return pd.DataFrame(rows).set_index("variant")


def evaluate_chronological_splits(
    bars_1m: pd.DataFrame,
    events: pd.DataFrame,
    config: BacktestConfig = BacktestConfig(),
    *,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> pd.DataFrame:
    """Evaluate all four stop/exit combinations independently on train, validation and test periods.

    Each period starts with the configured initial equity. Events are restricted to that
    period's timestamp interval. The test period must remain untouched during parameter
    selection; this function only reports metrics and does not choose a winner.
    """
    splits = chronological_splits(
        bars_1m, train_fraction=train_fraction, validation_fraction=validation_fraction
    )
    rows: list[dict] = []
    policy_configs = {
        "atr_fixed_2R": replace(config, stop_mode="atr", target_r_multiple=2.0, trend_exit_enabled=False),
        "atr_trend_exit": replace(config, stop_mode="atr", target_r_multiple=None, trend_exit_enabled=True),
        "structural_fixed_2R": replace(config, stop_mode="structural", target_r_multiple=2.0, trend_exit_enabled=False),
        "structural_trend_exit": replace(config, stop_mode="structural", target_r_multiple=None, trend_exit_enabled=True),
    }
    for split_name, split_bars in splits.items():
        start = split_bars.index[0]
        end = split_bars.index[-1] + pd.Timedelta(nanoseconds=1)
        split_events = events.loc[(events.index >= start) & (events.index < end)]
        for policy_name, policy_config in policy_configs.items():
            result = run_backtest(split_bars, split_events, policy_config)
            rows.append({"split": split_name, "variant": policy_name, **result["summary"]})
    return pd.DataFrame(rows).set_index(["split", "variant"])