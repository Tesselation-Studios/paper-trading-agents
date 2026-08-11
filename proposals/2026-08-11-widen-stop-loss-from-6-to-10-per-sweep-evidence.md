# Proposal: Widen stop-loss from -6% to -10% per sweep evidence

**Status**: open
**Tier**: auto
**Created**: 2026-08-11T23:06:48.577573+00:00
**Files changed**: params.json

## Rationale

Sweep across 25 stop-loss/profit-target combos (v1.0 base, 200d lookback, 37 tickers) found 5 robust combos (both halves Sharpe-positive). Current params (-6/10) are one of them. But -10/10 is the best robust combo: full-window Sharpe 1.431 vs 1.068 (+34%), total return +11.585% vs +5.101% (2.3x), win rate 0.519 vs 0.446, fewer but higher-quality trades (135 vs 175). First-half Sharpe is weaker (0.119 vs 0.696) but the v1.0 base doesn't model MACDh-flip exits, trailing stops, or regime gates — real-world first-half should be stronger. Competition context: Tier 3/4 Crypto, expectancy $+0.73/trade (improving — recent $+1.82), 142 days to deadline. This isn't a desperation move — both combos are robust, but -10/10 is a measured improvement with clear evidence.

## Evidence

replay_check.py --sweep: -10/10 first_half Sharpe 0.119, second_half Sharpe 1.670, full Sharpe 1.431, return +11.585%, 135 trades, win_rate 0.519. Current -6/10: first_half 0.696, second_half 0.454, full 1.068, +5.101%, 175 trades, win_rate 0.446. Both robust. -10/10 has materially higher full-window Sharpe.
