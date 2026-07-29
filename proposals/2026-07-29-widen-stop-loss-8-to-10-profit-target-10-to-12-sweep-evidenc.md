# Proposal: Widen stop-loss -8% to -10%, profit target 10% to 12% (sweep evidence)

**Status**: open
**Tier**: auto
**Created**: 2026-07-29T23:04:03.714378+00:00
**Files changed**: params.json

## Rationale

Sweep of 25 stop×target combos (26 tickers, 200d lookback) on v1.0-style strategy: current params (-8% stop, 10% target) produce Sharpe 2.28 but second-half Sharpe only 1.065 — the weakest of any candidate in its stop-loss tier. Every -10% and -12% candidate beats it on second-half consistency. Best moderate candidate: -10% stop / 12% target with full Sharpe 2.504, first-half 2.985, second-half 1.886 (both independently positive, robust=true), 113 trades, 52.2% win rate. The -12/15 candidate has even better second-half (2.456) but fewer trades (81) — going to -10/12 first is the conservative step. Directional signal is clear: -8% is too tight. Competition context: Tier 1/4 Stocks (22 trades, $+0.33/trade), improving trend (recent $+1.14/trade), 155 days to deadline.

## Evidence

scripts/replay_check.py --sweep: current -8/10: Sharpe=2.28, first=2.836, second=1.065, 140 trades. Proposed -10/12: Sharpe=2.504, first=2.985, second=1.886, 113 trades. Both halves independently positive. All -10% and -12% stop candidates have second-half Sharpe >1.8 vs -8% cluster 0.85-1.07.
