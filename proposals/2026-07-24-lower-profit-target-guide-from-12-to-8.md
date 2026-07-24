# Proposal: Lower profit target guide from 12% to 8%

**Status**: open
**Tier**: auto
**Created**: 2026-07-24T23:05:50.114018+00:00
**Files changed**: params.json

## Rationale

Sweep of 25 stop/profit-target combos on 8 tickers (200-day window, v1.0 baseline) shows (-10.0, 8.0) is the global optimum: Sharpe 1.912 vs current 1.379 at (-10.0, 12.0). Both halves independently Sharpe-positive (first 3.17, second 1.204). The direction is unambiguous — every lower-profit-target variant beats its higher-target counterpart at the same stop. In live v1.8 the profit_target is a guide (MACDh flips + trailing stops handle actual exits), so the measured 39% improvement overstates the real impact — but the direction is correct, and the evidence clears the split-window bar.

Competition context: Tier 1/4 (Stocks), 17 trades, $+0.00 expectancy, improving trend ($+1.50/trade recent), 160 days to deadline. Split-window also shows v1.1 (simpler, no scale-into-winners) at Sharpe 2.120 vs v1.7/v1.8 at 0.709-0.783 — a 67% degradation worth revisiting with more live data, but v1.7 proxy is rule-based not LLM-driven, so the gap may be narrower in production. Holding that observation for now.

## Evidence

replay_check.py --sweep: (-10.0, 8.0) Sharpe 1.912, first_half 3.17, second_half 1.204, robust=true, 52 trades, +4.74% return. Current (-10.0, 12.0): Sharpe 1.379, first_half 2.302, second_half 1.024, 42 trades, +3.60% return. Next-best (-10.0, 10.0): Sharpe 1.729, first_half 3.18, second_half 0.836. All three pass both-halves-positive bar; 8.0% target is the global optimum.
