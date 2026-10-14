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
    target_r_multiple: float | None = 2.0
    max_notional_leverage: float = 2.0
    max_holding_bars: int | None = None
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
    assumed paid by longs and received by shorts at each bar carrying a funding rate.
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

    signal_map = signals[["signal", "atr"]].to_dict("index")
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

        # Check exits against this bar only for positions carried into the bar.
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
                raw_exit = position["stop"] if config.intrabar_policy in ("adverse", "stop_first") else position["target"]
            elif stop_hit:
                exit_reason, raw_exit = "stop", position["stop"]
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

        # A signal timestamp means information became available then; enter no earlier than next bar.
        # Use previous timestamp's signal so a bar cannot fill using a signal revealed at its own close.
        # Re-evaluate the daily lockout after any exit/funding changes on this bar.
        if equity <= day_start_equity * (1 - config.daily_loss_limit_fraction):
            daily_locked = True
        if position is None and not daily_locked and i > 0:
            previous_ts = bars_1m.index[i - 1]
            event = signal_map.get(previous_ts)
            if event and int(event["signal"]) != 0 and pd.notna(event["atr"]) and float(event["atr"]) > 0:
                side = int(event["signal"])
                entry_raw = float(bar["open"])
                entry_fill = entry_raw * (1 + slippage_rate if side == 1 else 1 - slippage_rate)
                stop_distance = float(event["atr"]) * config.stop_atr_multiple
                stop = entry_fill - side * stop_distance
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

        equity_rows.append({"timestamp": ts, "equity": equity, "daily_locked": daily_locked})

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