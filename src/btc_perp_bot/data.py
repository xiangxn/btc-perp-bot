"""Historical candle loading, validation and causal timeframe aggregation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


def load_ohlcv_csv(path: str | Path, *, timestamp_column: str = "timestamp") -> pd.DataFrame:
    """Load UTC OHLCV from CSV; timestamps represent candle-open times.

    The returned frame has a sorted, unique UTC DatetimeIndex named ``timestamp``.
    """
    frame = pd.read_csv(path)
    if timestamp_column not in frame.columns:
        raise ValueError(f"missing timestamp column: {timestamp_column}")
    missing = set(OHLCV_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    timestamps = pd.to_datetime(frame.pop(timestamp_column), utc=True, errors="coerce")
    if timestamps.isna().any():
        raise ValueError("timestamp column contains invalid timestamps")
    frame.index = pd.DatetimeIndex(timestamps, name="timestamp")
    for column in OHLCV_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.sort_index()
    validate_ohlcv(frame)
    return frame


def validate_ohlcv(frame: pd.DataFrame, *, require_regular_minutes: bool = False) -> None:
    """Raise ValueError for malformed OHLCV data.

    Missing timestamps are allowed by default and should be reported by the caller; duplicate
    timestamps, null/non-finite values and impossible OHLC relationships are rejected.
    """
    missing = set(OHLCV_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {sorted(missing)}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("index must be a DatetimeIndex")
    if frame.index.tz is None:
        raise ValueError("timestamps must be timezone-aware; use UTC")
    if frame.index.has_duplicates:
        raise ValueError("duplicate candle timestamps found")
    if not frame.index.is_monotonic_increasing:
        raise ValueError("timestamps must be sorted ascending")
    values = frame[list(OHLCV_COLUMNS)]
    if values.isna().any().any():
        raise ValueError("OHLCV data contains null or non-numeric values")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("OHLCV data contains infinite values")
    if (frame["volume"] < 0).any():
        raise ValueError("volume cannot be negative")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("high is below another OHLC value")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("low is above another OHLC value")
    if require_regular_minutes and len(frame) > 1:
        deltas = frame.index.to_series().diff().dropna()
        if not (deltas == pd.Timedelta(minutes=1)).all():
            raise ValueError("1m candle data has gaps or irregular timestamps")


def resample_ohlcv(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate candle-open-indexed OHLCV into a larger timeframe.

    Supported targets are 5m and 15m. Only complete buckets are returned; a bucket is
    complete when it contains exactly 5 or 15 one-minute bars. This avoids partial candles
    being mistaken for completed signals. Input should be 1m data.
    """
    validate_ohlcv(frame)
    minutes_by_target = {"5min": 5, "5m": 5, "15min": 15, "15m": 15}
    if timeframe not in minutes_by_target:
        raise ValueError("timeframe must be one of: 5m, 5min, 15m, 15min")
    minutes = minutes_by_target[timeframe]
    if len(frame) == 0:
        return pd.DataFrame(columns=list(OHLCV_COLUMNS), index=pd.DatetimeIndex([], tz="UTC", name="timestamp"))
    # UTC minute boundaries; labels represent each bucket's opening time.
    grouped = frame[list(OHLCV_COLUMNS)].resample(
        f"{minutes}min", label="left", closed="left", origin="start_day"
    ).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    counts = frame["close"].resample(
        f"{minutes}min", label="left", closed="left", origin="start_day"
    ).count()
    result = grouped.loc[counts == minutes].dropna(subset=list(OHLCV_COLUMNS))
    result.index.name = "timestamp"
    return result