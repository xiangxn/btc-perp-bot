"""Command-line entry point for reproducible local CSV backtests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from btc_perp_bot.backtest import BacktestConfig, run_backtest
from btc_perp_bot.data import load_ohlcv_csv, resample_ohlcv
from btc_perp_bot.validation import compare_exit_policies, evaluate_chronological_splits
from btc_perp_bot.strategy import (
    StrategyConfig,
    apply_1m_execution_filter,
    generate_signals,
    to_execution_events,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the baseline BTC perpetual breakout strategy")
    parser.add_argument("--input", required=True, help="1m OHLCV CSV with timestamp,open,high,low,close,volume")
    parser.add_argument("--output-dir", default="backtest-output", help="Directory for trades, equity and summary")
    parser.add_argument("--initial-equity", type=float, default=300.0)
    parser.add_argument("--risk-fraction", type=float, default=0.005)
    parser.add_argument("--daily-loss-limit", type=float, default=0.015)
    parser.add_argument("--fee-rate", type=float, default=0.0005, help="Fee per side as a decimal fraction")
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--adx-min", type=float, default=20.0)
    parser.add_argument("--max-chase-atr", type=float, default=0.25, help="Reject entry if it has chased beyond this multiple of 5m ATR")
    parser.add_argument("--opposing-impulse-atr", type=float, default=1.0, help="Reject entry if recent opposing 1m impulse exceeds this multiple of 1m ATR")
    parser.add_argument("--target-r", type=float, default=2.0, help="Set 0 to disable fixed target")
    args = parser.parse_args()

    bars_1m = load_ohlcv_csv(args.input)
    if len(bars_1m) < 15:
        raise SystemExit("Input must contain at least 15 one-minute bars.")
    bars_5m = resample_ohlcv(bars_1m, "5m")
    bars_15m = resample_ohlcv(bars_1m, "15m")
    if bars_5m.empty or bars_15m.empty:
        raise SystemExit("Not enough complete 5m/15m candles in input.")

    strategy_config = StrategyConfig(adx_min=args.adx_min)
    signals_5m = generate_signals(bars_5m, bars_15m, strategy_config)
    events = to_execution_events(signals_5m)
    events = apply_1m_execution_filter(
        events,
        bars_1m,
        signals_5m,
        max_chase_atr=args.max_chase_atr,
        opposing_impulse_atr=args.opposing_impulse_atr,
    )
    backtest_config = BacktestConfig(
        initial_equity=args.initial_equity,
        risk_fraction=args.risk_fraction,
        daily_loss_limit_fraction=args.daily_loss_limit,
        fee_rate=args.fee_rate,
        slippage_bps=args.slippage_bps,
        target_r_multiple=args.target_r if args.target_r > 0 else None,
    )
    result = run_backtest(bars_1m, events, backtest_config)
    exit_comparison = compare_exit_policies(bars_1m, events, backtest_config)
    split_comparison = evaluate_chronological_splits(bars_1m, events, backtest_config)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result["trades"].to_csv(output_dir / "trades.csv", index=False)
    result["equity_curve"].to_csv(output_dir / "equity_curve.csv")
    exit_comparison.to_csv(output_dir / "exit_policy_comparison.csv")
    split_comparison.to_csv(output_dir / "chronological_split_comparison.csv")
    (output_dir / "summary.json").write_text(
        json.dumps(result["summary"], indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps(result["summary"], indent=2, allow_nan=False))


if __name__ == "__main__":
    main()