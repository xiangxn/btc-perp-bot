"""Causal technical indicators used by the baseline strategy."""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Return an exponentially weighted moving average.

    Values use only current and prior observations. Early values are present, so callers
    should enforce sufficient warm-up history before generating signals.
    """
    _validate_period(period)
    return series.astype(float).ewm(span=period, adjust=False, min_periods=period).mean()


def true_range(frame: pd.DataFrame) -> pd.Series:
    """Calculate true range from high, low and previous close."""
    _require_columns(frame, {"high", "low", "close"})
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    previous_close = frame["close"].astype(float).shift(1)
    parts = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    )
    return parts.max(axis=1, skipna=True).where(previous_close.notna(), high - low)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Return Wilder-style ATR using an exponentially smoothed true range."""
    _validate_period(period)
    tr = true_range(frame)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """Return ADX using Wilder-style smoothing; output is in the 0–100 range."""
    _validate_period(period)
    _require_columns(frame, {"high", "low", "close"})
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    smoothed_tr = true_range(frame).ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    plus_di = 100 * plus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / smoothed_tr.replace(0, float('nan'))
    minus_di = 100 * minus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean() / smoothed_tr.replace(0, float('nan'))
    denominator = (plus_di + minus_di).replace(0, float('nan'))
    dx = 100 * (plus_di - minus_di).abs() / denominator
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().clip(0, 100)


def _validate_period(period: int) -> None:
    if not isinstance(period, int) or period < 1:
        raise ValueError("period must be a positive integer")


def _require_columns(frame: pd.DataFrame, required: set[str]) -> None:
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")