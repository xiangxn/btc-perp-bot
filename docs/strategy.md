# Strategy B — BTC Perpetual Trend Breakout

## Objective and boundaries

This is the first research baseline, not a claim of profitability. It uses a 15m trend regime, a 5m breakout trigger, and a 1m execution/chase filter. Mean reversion and hybrid switching are explicitly out of scope for this first version.

## Signal specification

1. **15m regime:** long bias when EMA20 > EMA50, EMA20 is rising versus its previous completed value, and ADX(14) >= the configured threshold. Short bias is symmetric.
2. **5m trigger:** long when the completed 5m close breaks above the highest high of the preceding 12 completed 5m candles; short when it breaks below the preceding 12-candle low. The current candle is excluded from the lookback.
3. **1m execution filter:** reject entries after an excessive move from the breakout level or when a strong completed 1m impulse points against the intended direction. Thresholds must be configured and tested rather than tuned on the final test set.
4. **Execution timing:** a signal is known only after its candle closes. The earliest simulated entry is the next executable 1m bar, with adverse slippage and fees.

## Risk and exits

- Starting simulation equity: 300 USDT.
- Risk budget: 0.5% of current equity per trade before costs; actual quantity must account for stop distance, fees, slippage and venue quantity constraints.
- Daily loss lockout: 1.5% of start-of-day equity. No new entries after the limit is reached.
- At most one directional position; no martingale and no adding to a losing position.
- Compare structural stops with ATR-based stops as separate variants.
- Compare fixed 2R take-profit with a trend-following exit as separate variants.
- Stop gaps must fill at the next realistically executable price, not magically at the stop price.
- If stop and target are both touched in one candle and lower-resolution data cannot disambiguate order, use the adverse assumption or flag the trade as ambiguous.

## Cost model

The backtest must include entry and exit fees, spread/slippage, funding payments, execution delay and stop-fill variance. Funding timestamps and sign convention must be documented. Leverage changes required margin, not the pre-defined trade risk budget.

## Validation protocol

1. Validate source timestamps, missing bars, duplicate bars, and OHLC invariants.
2. Keep all indicator inputs causal; never use an unfinished higher-timeframe candle.
3. Split data chronologically into development, validation and untouched test periods.
4. Report net return, max drawdown, Profit Factor, win rate, average win/loss, expectancy, longest losing streak, holding time, fee/funding impact, and separate long/short results.
5. Test sensitivity to realistic cost increases and parameter perturbations. Prefer stable parameter neighborhoods over a single optimum.
6. Paper trade out-of-sample before considering live execution.

## Initial parameters (baseline, not optimized)

| Parameter | Initial value |
| --- | ---: |
| Trend EMA fast / slow | 20 / 50 (15m) |
| ADX period | 14 (15m) |
| Breakout lookback | 12 completed candles (5m) |
| Risk per trade | 0.5% equity |
| Daily loss lockout | 1.5% start-of-day equity |
| Fixed target comparison | 2R |

Any parameter change must be recorded as a distinct experiment. Do not use the final test period to select parameters.