# Proposal: v1.11: Revert to v1.1 rules + sweep-optimized -8/10 stop/target

**Status**: applied
**Tier**: auto
**Created**: 2026-07-27T23:04:05.126432+00:00
**Files changed**: strategy.md, params.json

## Rationale

v1.7's scale-into-winners (added post-v1.1) underperforms v1.1 on every risk-adjusted metric in split-window backtest on the current 18-ticker universe: v1.1 Sharpe 3.535 vs v1.7 Sharpe 1.893 (1.87x), v1.1 MaxDD -1.24% vs v1.7 -3.88% (3.1x better), v1.1 win rate 60.7% vs 54.9%. Both halves robust for both variants — but v1.1's first-half Sharpe 1.439 vs v1.7's 0.607, second-half 5.340 vs 3.136. Total return slightly favors v1.7 (9.21% vs 7.18%) but at unacceptable risk cost.\n\nIndependent sweep evidence (v1.0 baseline, same universe) confirms -8% stop/10% target is the optimal param pair: Sharpe 2.045 vs current -10/8 Sharpe 1.573 (+30%), return 8.50% vs 6.01% (+41%), both halves robust (1H 4.123, 2H 0.819).\n\nCompetition context: Tier 1/4, 21 trades, +\/bin/bash.50/trade lifetime, improving trend (+\.23 recent), 157 days to deadline. This is a risk-efficiency improvement, not a desperation move.\n\nChanges: (1) strategy.md — remove scale-into-winners prose, revert entry/exit philosophy to v1.1's MACDh-flip exit + regime-gated entries, bump version to v1.11. (2) params.json — stop_loss_pct: -8.0, profit_target_pct: 10.0, strategy_version: stonks.strat:v1.11.

## Evidence

split-window (103 trading days, 18 tickers): v1.1 Sharpe 3.535 (1H 1.439 / 2H 5.340) vs v1.7 Sharpe 1.893 (1H 0.607 / 2H 3.136). sweep (same universe, 200d lookback): -8/10 combo Sharpe 2.045 (1H 4.123 / 2H 0.819) vs current -10/8 Sharpe 1.573 (1H 3.030 / 2H 0.725), 96 trades, 8.50% return.

## Resolution

applied at 2026-07-27T23:32:28.480260+00:00
