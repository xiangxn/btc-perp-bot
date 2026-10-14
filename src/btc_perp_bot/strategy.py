"""Baseline 15m trend filter and 5m breakout signal generation."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from btc_perp_bot.indicators import adx, atr, ema


@dataclass(frozen=True)
class StrategyConfig:
    breakout_lookback: int = 12
    fast_ema: int = 20
    slow_ema: int = 50
    adx_period: int = 14
    adx_min: float = 20.0

    def __post_init__(self) -> None:
        if self.breakout_lookback < 1:
            raise ValueError("breakout_lookback must be positive")
        if min(self.fast_ema, self.slow_ema, self.adx_period) < 1:
            raise ValueError("indicator periods must be positive")
        if self.fast_ema >= self.slow_ema:
            raise ValueError("fast_ema must be smaller than slow_ema")
        if not 0 <= self.adx_min <= 100:
            raise ValueError("adx_min must be between 0 and 100")


def generate_signals(
    bars_5m: pd.DataFrame,
    bars_15m: pd.DataFrame,
    config: StrategyConfig = StrategyConfig(),
) -> pd.DataFrame:
    """Return 5m bars with causal trend features and {-1, 0, +1} signals.

    Both input indices must be sorted DatetimeIndex values representing candle OPEN times.
    A 15m candle's features become available only after its close, so they are shifted one
    15m bar before being aligned to 5m candle-open timestamps. A 5m signal is based on the
    completed 5m candle and must be executed no earlier than a later 1m bar.
    """
    _validate_bars(bars_5m, "5m")
    _validate_bars(bars_15m, "15m")
    if not isinstance(bars_5m.index, pd.DatetimeIndex) or not isinstance(
        bars_15m.index, pd.DatetimeIndex
    ):
        raise ValueError("bars must use a DatetimeIndex of candle-open timestamps")
    if not bars_5m.index.is_monotonic_increasing or not bars_15m.index.is_monotonic_increasing:
        raise ValueError("bar timestamps must be sorted ascending")

    trend = bars_15m.copy()
    trend["ema_fast"] = ema(trend["close"], config.fast_ema)
    trend["ema_slow"] = ema(trend["close"], config.slow_ema)
    trend["adx"] = adx(trend, config.adx_period)
    trend["ema_fast_rising"] = trend["ema_fast"] > trend["ema_fast"].shift(1)
    trend["trend_long"] = (
        (trend["ema_fast"] > trend["ema_slow"])
        & trend["ema_fast_rising"]
        & (trend["adx"] >= config.adx_min)
    )
    trend["trend_short"] = (
        (trend["ema_fast"] < trend["ema_slow"])
        & (~trend["ema_fast_rising"])
        & (trend["adx"] >= config.adx_min)
    )
    # At a 5m candle open, only 15m candles whose close timestamp is <= this open are known.
    trend_features = trend[["trend_long", "trend_short", "adx"]].shift(1)
    aligned = trend_features.reindex(
        trend_features.index.union(bars_5m.index)
    ).sort_index().ffill().reindex(bars_5m.index)

    result = bars_5m.copy()
    result["trend_long"] = aligned["trend_long"].fillna(False).astype(bool)
    result["trend_short"] = aligned["trend_short"].fillna(False).astype(bool)
    result["trend_adx"] = aligned["adx"]
    result["prior_high"] = result["high"].shift(1).rolling(
        config.breakout_lookback, min_periods=config.breakout_lookback
    ).max()
    result["prior_low"] = result["low"].shift(1).rolling(
        config.breakout_lookback, min_periods=config.breakout_lookback
    ).min()
    result["signal"] = 0
    long_mask = result["trend_long"] & (result["close"] > result["prior_high"])
    short_mask = result["trend_short"] & (result["close"] < result["prior_low"])
    result.loc[long_mask, "signal"] = 1
    result.loc[short_mask, "signal"] = -1
    result["signal"] = result["signal"].astype("int8")
    return result


def _validate_bars(frame: pd.DataFrame, timeframe: str) -> None:
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"{timeframe} bars missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError(f"{timeframe} bars must not be empty")
    if frame[list(required)].isna().any().any():
        raise ValueError(f"{timeframe} OHLCV columns must not contain null values")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError(f"{timeframe} bar has high below another OHLC value")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError(f"{timeframe} bar has low above another OHLC value")

def to_execution_events(signals_5m: pd.DataFrame) -> pd.DataFrame:
    """Convert 5m candle-close signals to backtest events indexed by availability time.

    The input index must contain 5m candle-open timestamps. Events are shifted to the
    corresponding candle close (open + 5 minutes); that timestamp is also the next 1m candle
    open when the data is aligned to UTC minute boundaries. ATR and optional structural stop
    references are attached to the event.
    """
    required = {"open", "high", "low", "close", "volume", "signal"}
    missing = required.difference(signals_5m.columns)
    if missing:
        raise ValueError(f"5m signals missing columns: {sorted(missing)}")
    if not isinstance(signals_5m.index, pd.DatetimeIndex) or signals_5m.index.tz is None:
        raise ValueError("5m signals must use a timezone-aware DatetimeIndex")
    if not signals_5m.index.is_monotonic_increasing or signals_5m.index.has_duplicates:
        raise ValueError("5m signal timestamps must be sorted and unique")
    events = pd.DataFrame(index=signals_5m.index + pd.Timedelta(minutes=5))
    events["signal"] = signals_5m["signal"].astype("int8").to_numpy()
    events["atr"] = atr(signals_5m, 14).to_numpy()
    if {"prior_high", "prior_low"}.issubset(signals_5m.columns):
        prior_high = signals_5m["prior_high"].to_numpy()
        prior_low = signals_5m["prior_low"].to_numpy()
        signal_values = signals_5m["signal"].to_numpy()
        events["structural_stop"] = [
            prior_low[i] if signal_values[i] == 1 else
            prior_high[i] if signal_values[i] == -1 else float("nan")
            for i in range(len(signal_values))
        ]
    if {"trend_long", "trend_short"}.issubset(signals_5m.columns):
        events["exit_long"] = ~signals_5m["trend_long"].fillna(False).astype(bool).to_numpy()
        events["exit_short"] = ~signals_5m["trend_short"].fillna(False).astype(bool).to_numpy()
    events.index.name = "timestamp"
    return events

def apply_1m_execution_filter(
    events: pd.DataFrame,
    bars_1m: pd.DataFrame,
    signals_5m: pd.DataFrame,
    *,
    max_chase_atr: float = 0.25,
    opposing_impulse_atr: float = 1.0,
    impulse_lookback: int = 3,
) -> pd.DataFrame:
    """Reject 5m signals that are overextended or face a strong opposing 1m impulse.

    ``events`` comes from :func:`to_execution_events`. Only 1m candles completed by the event
    timestamp are used for the impulse calculation. The prospective entry open is read from
    the first 1m candle strictly after the event timestamp, matching the backtest's conservative
    next-bar execution convention. The filter rejects rather than guesses when warm-up data is
    unavailable. Thresholds are configurable research parameters, not optimized values.
    """
    if max_chase_atr < 0 or opposing_impulse_atr < 0 or impulse_lookback < 1:
        raise ValueError("filter thresholds must be nonnegative and lookback must be positive")
    if not isinstance(bars_1m.index, pd.DatetimeIndex) or bars_1m.index.tz is None:
        raise ValueError("bars_1m must use a timezone-aware DatetimeIndex")
    if not {"prior_high", "prior_low"}.issubset(signals_5m.columns):
        raise ValueError("signals_5m must contain prior_high and prior_low")
    filtered = events.copy()
    filtered["filter_reason"] = ""
    atr_1m = atr(bars_1m, 14)
    levels = signals_5m[["prior_high", "prior_low"]].copy()
    levels.index = levels.index + pd.Timedelta(minutes=5)
    for event_ts, event in filtered.iterrows():
        side = int(event["signal"])
        if side == 0:
            continue
        history = bars_1m.loc[bars_1m.index < event_ts]
        future = bars_1m.loc[bars_1m.index >= event_ts]
        if len(history) < max(14, impulse_lookback + 1) or future.empty:
            filtered.loc[event_ts, "signal"] = 0
            filtered.loc[event_ts, "filter_reason"] = "insufficient_warmup_or_execution_bar"
            continue
        atr5 = float(event["atr"]) if pd.notna(event["atr"]) else float("nan")
        atr1 = float(atr_1m.loc[history.index[-1]])
        if not pd.notna(atr5) or atr5 <= 0 or not pd.notna(atr1) or atr1 <= 0:
            filtered.loc[event_ts, "signal"] = 0
            filtered.loc[event_ts, "filter_reason"] = "invalid_atr"
            continue
        level_row = levels.loc[event_ts] if event_ts in levels.index else None
        if level_row is None:
            filtered.loc[event_ts, "signal"] = 0
            filtered.loc[event_ts, "filter_reason"] = "missing_breakout_level"
            continue
        level = float(level_row["prior_high"] if side == 1 else level_row["prior_low"])
        execution_open = float(future.iloc[0]["open"])
        chase_distance = max(0.0, side * (execution_open - level)) / atr5
        closes = history["close"].astype(float)
        impulse = float(closes.iloc[-1] - closes.iloc[-1 - impulse_lookback])
        opposing = side * impulse < -opposing_impulse_atr * atr1
        if chase_distance > max_chase_atr:
            filtered.loc[event_ts, "signal"] = 0
            filtered.loc[event_ts, "filter_reason"] = "chase_limit"
        elif opposing:
            filtered.loc[event_ts, "signal"] = 0
            filtered.loc[event_ts, "filter_reason"] = "opposing_1m_impulse"
        else:
            filtered.loc[event_ts, "filter_reason"] = "accepted"
    return filtered