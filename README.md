# BTC Perpetual Trading Bot

Research-first automated trading system for BTC perpetual futures.

> **Status:** research/backtesting only. Live trading is intentionally not implemented yet.
> The goal is to test whether a strategy has robust, cost-adjusted out-of-sample performance before risking real capital.

## Initial scope

- Strategy B only: trend-following breakout baseline.
- Timeframes: 15m trend filter, 5m breakout signal, 1m execution/chase filter (default: 0.25 × 5m ATR chase cap and 1.0 × 1m ATR opposing impulse threshold).
- Start capital for simulations: 300 USDT.
- Per-trade risk budget: 0.5% of current equity, subject to exchange constraints and minimum order sizes.
- Daily loss lockout: 1.5% of the day's starting equity.
- Never add to a losing position; at most one directional position at a time.
- Include fees, slippage, funding, delayed execution and stop-fill uncertainty in backtests.
- No live orders until out-of-sample validation and paper trading are complete.

The aspirational 1,000x capital goal is not a performance assumption or guarantee. The system must prioritize risk control and honest evaluation over return targets.

## Planned workflow

1. Obtain BTC perpetual 1m OHLCV history; derive 5m and 15m candles without lookahead.
2. Implement and test indicators and the baseline breakout strategy.
3. Compare ATR vs structural stops and fixed 2R vs trend-regime exits as four separate variants.
4. Run cost-aware backtests with chronological train/validation/test splits.
5. Review net PnL, max drawdown, Profit Factor, win/loss distribution, losing streaks, fee share, and long/short breakdown.
6. Paper trade; only then consider a tightly limited live pilot.

## Repository layout

- `docs/strategy.md` — initial strategy specification and validation protocol.
- `src/btc_perp_bot/` — research/backtest implementation.
- `tests/` — unit and regression tests.

## Development

Python 3.11+ is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
```

The initial implementation uses local CSV data so the backtest can be reproduced independently of an exchange API. Expected candle columns: `timestamp, open, high, low, close, volume`; timestamps must be UTC and consistently represent candle open times. An optional `funding_rate` column is interpreted as the rate applied at that bar's timestamp; positive rates are paid by longs and received by shorts. Confirm the source exchange's funding timestamp and sign convention before interpreting results.

Download public Binance USDⓈ-M BTCUSDT perpetual data (no API key required):

```bash
python scripts/download_binance_usdm.py --start 2025-01-01T00:00:00Z --end 2025-04-01T00:00:00Z --output data/btcusdt_perp_1m.csv
```

The end timestamp is exclusive. The downloader writes UTC candle-open timestamps and a `funding_rate` column that is zero except at aligned funding settlements. Verify source alignment and funding sign conventions before relying on results.

Run a baseline backtest:

```bash
btc-perp-backtest --input data/btcusdt_perp_1m.csv --output-dir backtest-output
```

The command writes `trades.csv`, `equity_curve.csv`, `summary.json`, `exit_policy_comparison.csv` (ATR or structural stop crossed with fixed 2R or trend-regime exit), and `chronological_split_comparison.csv` (chronological 60% train / 20% validation / 20% test, each evaluated independently). The test split must remain untouched during parameter selection. Default parameters are a starting point for research, not optimized settings.

## Safety and research rules

- Signals may only use completed candles.
- Orders are filled no earlier than the next executable bar, with conservative cost assumptions.
- Stop gaps and intrabar stop/target ambiguity must not be resolved optimistically.
- All strategy variants and parameter choices must be recorded explicitly.
- Report out-of-sample results separately; do not select a strategy using the final test period.
- Exchange precision, minimum quantity/notional, margin, liquidation buffer and fee tier must be checked before any live execution work.