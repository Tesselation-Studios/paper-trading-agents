# Proposal: v1.8: Revert to v1.1 core (drop scale-into-winners) + tighten stop/target to (-6%, 10%)

**Status**: applied
**Tier**: auto
**Created**: 2026-08-05T23:05:42.251490+00:00
**Files changed**: strategy.md, params.json

## Rationale

Two independent evidence streams converge:

1. SPLIT-WINDOW: v1.7 (live = v1.1 + scale-into-winners) degrades v1.1 substantially. v1.1 full Sharpe 4.618 (H1 3.323, H2 4.614), v1.7 full Sharpe 2.099 (H1 0.219, H2 2.362). The scale-into-winners layer added 548 decisions vs v1.1's 282 but produced HALF the Sharpe and 37% less return (+11.66% vs +18.58%) on the same core rules. First-half near-flat (Sharpe 0.219) is the smoking gun — scale-in is actively destructive, not just unnecessary.

2. PARAM SWEEP: (-6%, 10%) is the top v1.0-baseline combo: Sharpe 3.982 (H1 3.863, H2 3.575) vs current (-10%, 12%) Sharpe 3.148 (H1 2.879, H2 2.844). Both halves positive, +26.5% Sharpe improvement. Tighter stop + lower target = more trades (198 vs 124), lower win rate (48.5% vs 56.5%) but better risk-adjusted return — the 'small and wide, high-rep' pattern.

Competition context: Tier 2/4 Shorting, expectancy +.07/trade (trend: improving, recent +.50), 148 days to deadline. These changes don't change competition-mode sizing — they change the decision quality feeding into it.

## Evidence

replay_check --split-window: v1.1 vs v1.7 (36 tickers, 104 trading days, lookback 200d). v1.1: full Sharpe 4.618 (H1 3.323/H2 4.614), return +18.58%, max DD -2.65%, 128 trades, win rate 53.9%. v1.7: full Sharpe 2.099 (H1 0.219/H2 2.362), return +11.66%, max DD -3.57%, 111 trades, win rate 45.9%. Both halves Sharpe-positive in both variants, but v1.1 dominates on every metric.

replay_check --sweep: 5x5 stop/target grid (v1.0 baseline). (-6%, 10%): Sharpe 3.982 (H1 3.863/H2 3.575), return +29.84%, 198 trades, win rate 48.5%. Current (-10%, 12%): Sharpe 3.148 (H1 2.879/H2 2.844), return +30.09%, 124 trades, win rate 56.5%. Both halves Sharpe-positive. Tighter stop/target trades off win rate for higher risk-adjusted return.

## Resolution

applied at 2026-08-05T23:33:34.278114+00:00
