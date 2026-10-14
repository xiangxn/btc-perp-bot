# BTC Perpetual Trading Bot

Research-first automated trading system for BTC perpetual futures.

> **Status:** research/backtesting only. Live trading is intentionally not implemented yet.
> The goal is to test whether a strategy has robust, cost-adjusted out-of-sample performance before risking real capital.

## Initial scope

- Strategy B only: trend-following breakout baseline.
- Timeframes: 15m trend filter, 5m breakout signal, 1m execution/chase filter.
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
3. Compare structural stops vs ATR stops, and fixed 2R targets vs trend-following exits.
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

The initial implementation will use local CSV data so the backtest can be reproduced independently of an exchange API. Expected candle columns: `timestamp, open, high, low, close, volume`; timestamps must be UTC and consistently represent candle open times. Funding data can be supplied separately or in an extended dataset.

## Safety and research rules

- Signals may only use completed candles.
- Orders are filled no earlier than the next executable bar, with conservative cost assumptions.
- Stop gaps and intrabar stop/target ambiguity must not be resolved optimistically.
- All strategy variants and parameter choices must be recorded explicitly.
- Report out-of-sample results separately; do not select a strategy using the final test period.
- Exchange precision, minimum quantity/notional, margin, liquidation buffer and fee tier must be checked before any live execution work.