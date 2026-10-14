"""Conservative bar-based backtest engine for research, not live execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from btc_perp_bot.data import validate_ohlcv


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 300.0
    risk_fraction: float = 0.005
    daily_loss_limit_fraction: float = 0.015
    fee_rate: float = 0.0005
    slippage_bps: float = 2.0
    stop_atr_multiple: float = 1.5
    stop_mode: Literal["atr", "structural"] = "atr"
    target_r_multiple: float | None = 2.0
    max_notional_leverage: float = 2.0
    max_holding_bars: int | None = None
    trend_exit_enabled: bool = False
    intrabar_policy: Literal["adverse", "stop_first"] = "adverse"

    def __post_init__(self) -> None:
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if not 0 < self.risk_fraction < 1:
            raise ValueError("risk_fraction must be between 0 and 1")
        if not 0 < self.daily_loss_limit_fraction < 1:
            raise ValueError("daily_loss_limit_fraction must be between 0 and 1")
        if self.fee_rate < 0 or self.slippage_bps < 0:
            raise ValueError("fees and slippage cannot be negative")
        if self.stop_mode not in ("atr", "structural"):
            raise ValueError("stop_mode must be atr or structural")
        if self.stop_atr_multiple <= 0 or self.max_notional_leverage <= 0:
            raise ValueError("stop multiple and leverage cap must be positive")
        if self.target_r_multiple is not None and self.target_r_multiple <= 0:
            raise ValueError("target_r_multiple must be positive or None")
        if self.max_holding_bars is not None and self.max_holding_bars < 1:
            raise ValueError("max_holding_bars must be positive or None")


def run_backtest(
    bars_1m: pd.DataFrame,
    signals: pd.DataFrame,
    config: BacktestConfig = BacktestConfig(),
) -> dict[str, pd.DataFrame | dict[str, float | int]]:
    """Run a single-position long/short backtest.

    ``signals`` is indexed by UTC timestamps when a signal is known (after the source candle
    closes), with columns ``signal`` (-1/0/+1) and ``atr``. Entries occur at the next 1m bar
    open. Stop distance is ATR * configured multiple, measured from actual simulated entry.
    This baseline has no partial fills, exchange quantity-step model or liquidation engine yet.
    Funding is applied when an optional ``funding_rate`` column is present; a positive rate is
    assumed paid by longs and received by shorts at each bar carrying a funding rate. For this
    reason, funding_rate must be zero except at actual funding settlement timestamps. The equity
    curve is marked to market and includes an estimated close fee while a position is open.
    """
    validate_ohlcv(bars_1m)
    if not isinstance(bars_1m.index, pd.DatetimeIndex) or bars_1m.index.tz is None:
        raise ValueError("bars_1m index must be timezone-aware timestamps")
    if not isinstance(signals.index, pd.DatetimeIndex) or signals.index.tz is None:
        raise ValueError("signals index must be timezone-aware timestamps")
    if not {"signal", "atr"}.issubset(signals.columns):
        raise ValueError("signals must contain signal and atr columns")
    if not bars_1m.index.is_monotonic_increasing or bars_1m.index.has_duplicates:
        raise ValueError("bars_1m timestamps must be sorted and unique")
    if not signals.index.is_monotonic_increasing or signals.index.has_duplicates:
        raise ValueError("signal timestamps must be sorted and unique")
    if not signals["signal"].isin([-1, 0, 1]).all():
        raise ValueError("signal values must be -1, 0 or 1")

    signal_map = signals.to_dict("index")
    equity = float(config.initial_equity)
    day_start_equity = equity
    current_day = None
    daily_locked = False
    position: dict | None = None
    trades: list[dict] = []
    equity_rows: list[dict] = []
    slippage_rate = config.slippage_bps / 10_000.0

    for i, (ts, bar) in enumerate(bars_1m.iterrows()):
        day = ts.date()
        if day != current_day:
            current_day = day
            if i > 0:
                previous_close = float(bars_1m.iloc[i - 1]["close"])
                day_start_equity = equity
                if position is not None:
                    day_start_equity += position["side"] * (previous_close - position["entry_fill"]) * position["qty"]
                    day_start_equity -= abs(previous_close * position["qty"]) * config.fee_rate
            else:
                day_start_equity = equity
            daily_locked = False
        if equity <= day_start_equity * (1 - config.daily_loss_limit_fraction):
            daily_locked = True

        # Funding is charged/credited at the bar timestamp if a rate is supplied.
        if position is not None and "funding_rate" in bars_1m.columns:
            rate = float(bar.get("funding_rate", 0.0))
            funding_cashflow = -position["side"] * position["qty"] * float(bar["open"]) * rate
            equity += funding_cashflow
            position["funding"] += funding_cashflow

        # A trend-exit event is known at this timestamp and can execute at this bar open.
        event_now = signal_map.get(ts)
        if position is not None and event_now is not None and config.trend_exit_enabled:
            should_exit = (
                position["side"] == 1 and bool(event_now.get("exit_long", False))
            ) or (
                position["side"] == -1 and bool(event_now.get("exit_short", False))
            )
            if should_exit:
                side = position["side"]
                raw_exit = float(bar["open"])
                exit_fill = raw_exit * (1 - slippage_rate if side == 1 else 1 + slippage_rate)
                exit_fee = abs(exit_fill * position["qty"]) * config.fee_rate
                gross = side * (exit_fill - position["entry_fill"]) * position["qty"]
                equity += gross - exit_fee
                trades.append({
                    "entry_time": position["entry_ts"], "exit_time": ts,
                    "side": "long" if side == 1 else "short", "qty": position["qty"],
                    "entry_price": position["entry_fill"], "exit_price": exit_fill,
                    "stop_price": position["stop"], "target_price": position["target"],
                    "gross_pnl": gross, "entry_fee": position["entry_fee"], "exit_fee": exit_fee,
                    "funding": position["funding"],
                    "net_pnl": gross - position["entry_fee"] - exit_fee + position["funding"],
                    "exit_reason": "trend_exit",
                })
                position = None

        # Check stop/target against this bar for positions carried into the bar.
        if position is not None and ts > position["entry_ts"]:
            stop_hit = (bar["low"] <= position["stop"] if position["side"] == 1
                        else bar["high"] >= position["stop"])
            target_hit = position["target"] is not None and (
                bar["high"] >= position["target"] if position["side"] == 1
                else bar["low"] <= position["target"]
            )
            exit_reason = None
            raw_exit = None
            if stop_hit and target_hit:
                exit_reason = "stop_and_target_same_bar"
                if position["side"] == 1:
                    raw_exit = min(float(position["stop"]), float(bar["open"]))
                else:
                    raw_exit = max(float(position["stop"]), float(bar["open"]))
            elif stop_hit:
                exit_reason = "stop"
                # A stop-market order can fill worse than its trigger after a gap.
                if position["side"] == 1:
                    raw_exit = min(float(position["stop"]), float(bar["open"]))
                else:
                    raw_exit = max(float(position["stop"]), float(bar["open"]))
            elif target_hit:
                exit_reason, raw_exit = "target", position["target"]
            elif config.max_holding_bars is not None and i - position["entry_i"] >= config.max_holding_bars:
                exit_reason, raw_exit = "time_exit", float(bar["close"])

            if exit_reason is not None:
                exit_fill = raw_exit * (1 - slippage_rate if position["side"] == 1 else 1 + slippage_rate)
                exit_fee = abs(exit_fill * position["qty"]) * config.fee_rate
                gross = position["side"] * (exit_fill - position["entry_fill"]) * position["qty"]
                # Entry fee was booked at entry; book gross PnL and exit fee only now.
                equity += gross - exit_fee
                trades.append({
                    "entry_time": position["entry_ts"],
                    "exit_time": ts,
                    "side": "long" if position["side"] == 1 else "short",
                    "qty": position["qty"],
                    "entry_price": position["entry_fill"],
                    "exit_price": exit_fill,
                    "stop_price": position["stop"],
                    "target_price": position["target"],
                    "gross_pnl": gross,
                    "entry_fee": position["entry_fee"],
                    "exit_fee": exit_fee,
                    "funding": position["funding"],
                    "net_pnl": gross - position["entry_fee"] - exit_fee + position["funding"],
                    "exit_reason": exit_reason,
                })
                position = None

        # Signal timestamps denote when the completed candle became available. With candle-open
        # timestamps, an event at ts may execute at this bar open, never at the prior bar close.
        # Re-evaluate realized loss before opening a new position.
        if equity <= day_start_equity * (1 - config.daily_loss_limit_fraction):
            daily_locked = True
        if position is None and not daily_locked:
            event = signal_map.get(ts)
            if event and int(event["signal"]) != 0 and pd.notna(event["atr"]) and float(event["atr"]) > 0:
                side = int(event["signal"])
                entry_raw = float(bar["open"])
                entry_fill = entry_raw * (1 + slippage_rate if side == 1 else 1 - slippage_rate)
                valid_stop = True
                if config.stop_mode == "structural":
                    structural_stop = event.get("structural_stop")
                    if structural_stop is None or pd.isna(structural_stop):
                        valid_stop = False
                        stop = entry_fill
                        stop_distance = 0.0
                    else:
                        stop = float(structural_stop)
                        stop_distance = side * (entry_fill - stop)
                        valid_stop = stop_distance > 0
                else:
                    stop_distance = float(event["atr"]) * config.stop_atr_multiple
                    stop = entry_fill - side * stop_distance
                if valid_stop:
                    target = (entry_fill + side * stop_distance * config.target_r_multiple
                              if config.target_r_multiple is not None else None)
                    risk_cash = equity * config.risk_fraction
                    # Budget round-trip entry fee conservatively in sizing; slippage is already
                    # reflected in fills but stop-gap loss can still exceed this risk budget.
                    per_unit_risk = stop_distance + entry_fill * config.fee_rate * 2
                    qty_by_risk = risk_cash / per_unit_risk
                    qty_by_notional = equity * config.max_notional_leverage / entry_fill
                    qty = min(qty_by_risk, qty_by_notional)
                    if qty > 0:
                        entry_fee = abs(entry_fill * qty) * config.fee_rate
                        equity -= entry_fee
                        position = {
                            "side": side, "qty": qty, "entry_ts": ts, "entry_i": i,
                            "entry_fill": entry_fill, "entry_fee": entry_fee, "stop": stop,
                            "target": target, "funding": 0.0,
                        }

        # Entry occurs at this bar open, so the remainder of the same bar can hit its stop/target.
        # If both are touched, use the adverse stop outcome because OHLC cannot reveal the path.
        if position is not None and position["entry_ts"] == ts:
            side = position["side"]
            stop_hit = (float(bar["low"]) <= position["stop"] if side == 1
                        else float(bar["high"]) >= position["stop"])
            target_hit = position["target"] is not None and (
                float(bar["high"]) >= position["target"] if side == 1
                else float(bar["low"]) <= position["target"]
            )
            if stop_hit or target_hit:
                if stop_hit:
                    exit_reason = "stop_and_target_same_bar" if target_hit else "stop"
                    raw_exit = (min(float(position["stop"]), float(bar["open"])) if side == 1
                                else max(float(position["stop"]), float(bar["open"])))
                else:
                    exit_reason, raw_exit = "target", float(position["target"])
                exit_fill = raw_exit * (1 - slippage_rate if side == 1 else 1 + slippage_rate)
                exit_fee = abs(exit_fill * position["qty"]) * config.fee_rate
                gross = side * (exit_fill - position["entry_fill"]) * position["qty"]
                equity += gross - exit_fee
                trades.append({
                    "entry_time": position["entry_ts"], "exit_time": ts,
                    "side": "long" if side == 1 else "short", "qty": position["qty"],
                    "entry_price": position["entry_fill"], "exit_price": exit_fill,
                    "stop_price": position["stop"], "target_price": position["target"],
                    "gross_pnl": gross, "entry_fee": position["entry_fee"], "exit_fee": exit_fee,
                    "funding": position["funding"],
                    "net_pnl": gross - position["entry_fee"] - exit_fee + position["funding"],
                    "exit_reason": exit_reason,
                })
                position = None

        # Mark equity to market for drawdown reporting and daily loss lockout.
        marked_equity = equity
        if position is not None:
            marked_equity += position["side"] * (float(bar["close"]) - position["entry_fill"]) * position["qty"]
            marked_equity -= abs(float(bar["close"]) * position["qty"]) * config.fee_rate
        if marked_equity <= day_start_equity * (1 - config.daily_loss_limit_fraction):
            daily_locked = True
        equity_rows.append({"timestamp": ts, "equity": marked_equity, "cash_equity": equity, "daily_locked": daily_locked})

    # Mark open position to the final close and force a conservative final-bar exit.
    if position is not None and len(bars_1m):
        ts = bars_1m.index[-1]
        raw_exit = float(bars_1m.iloc[-1]["close"])
        exit_fill = raw_exit * (1 - slippage_rate if position["side"] == 1 else 1 + slippage_rate)
        exit_fee = abs(exit_fill * position["qty"]) * config.fee_rate
        gross = position["side"] * (exit_fill - position["entry_fill"]) * position["qty"]
        equity += gross - exit_fee
        trades.append({
            "entry_time": position["entry_ts"], "exit_time": ts,
            "side": "long" if position["side"] == 1 else "short", "qty": position["qty"],
            "entry_price": position["entry_fill"], "exit_price": exit_fill,
            "stop_price": position["stop"], "target_price": position["target"],
            "gross_pnl": gross, "entry_fee": position["entry_fee"], "exit_fee": exit_fee,
            "funding": position["funding"],
            "net_pnl": gross - position["entry_fee"] - exit_fee + position["funding"],
            "exit_reason": "end_of_data",
        })
        if equity_rows:
            equity_rows[-1]["equity"] = equity
            equity_rows[-1]["cash_equity"] = equity

    trades_frame = pd.DataFrame(trades)
    equity_frame = pd.DataFrame(equity_rows).set_index("timestamp") if equity_rows else pd.DataFrame(columns=["equity", "daily_locked"])
    summary = _summarize(trades_frame, equity_frame, config.initial_equity, equity)
    return {"trades": trades_frame, "equity_curve": equity_frame, "summary": summary}


def _summarize(trades: pd.DataFrame, curve: pd.DataFrame, initial_equity: float, final_equity: float) -> dict[str, float | int]:
    pnl = trades["net_pnl"] if not trades.empty else pd.Series(dtype=float)
    winners = pnl[pnl > 0]
    losers = pnl[pnl < 0]
    gross_profit = float(winners.sum())
    gross_loss = float(-losers.sum())
    if not curve.empty:
        values = pd.concat([pd.Series([initial_equity]), curve["equity"].reset_index(drop=True)], ignore_index=True)
        drawdown = values / values.cummax() - 1
        max_drawdown = float(-drawdown.min())
    else:
        max_drawdown = 0.0
    return {
        "initial_equity": float(initial_equity),
        "final_equity": float(final_equity),
        "net_return_pct": (float(final_equity) / initial_equity - 1) * 100,
        "max_drawdown_pct": max_drawdown * 100,
        "trade_count": int(len(trades)),
        "win_rate_pct": float((pnl > 0).mean() * 100) if len(pnl) else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (None if gross_profit > 0 else 0.0),
        "average_trade_net_pnl": float(pnl.mean()) if len(pnl) else 0.0,
        "average_win": float(winners.mean()) if len(winners) else 0.0,
        "average_loss": float(losers.mean()) if len(losers) else 0.0,
        "max_consecutive_losses": _max_consecutive_losses(pnl.tolist()),
        "total_fees": float(trades["entry_fee"].sum() + trades["exit_fee"].sum()) if not trades.empty else 0.0,
        "total_funding_cashflow": float(trades["funding"].sum()) if not trades.empty else 0.0,
        "total_gross_pnl": float(trades["gross_pnl"].sum()) if not trades.empty else 0.0,
        "total_trade_net_pnl": float(pnl.sum()) if len(pnl) else 0.0,
        "fees_as_pct_of_positive_gross_pnl": (
            float((trades["entry_fee"].sum() + trades["exit_fee"].sum()) / trades.loc[trades["gross_pnl"] > 0, "gross_pnl"].sum() * 100)
            if not trades.empty and trades.loc[trades["gross_pnl"] > 0, "gross_pnl"].sum() > 0 else 0.0
        ),
    }


def _max_consecutive_losses(pnl: list[float]) -> int:
    longest = current = 0
    for value in pnl:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest