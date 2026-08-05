# Proposal: Volatility-scaled trailing stop (TRAIL_K=40)

**Status**: applied
**Tier**: review_required
**Created**: 2026-07-29T01:06:56.322028+00:00
**Files changed**: scripts/guardrails.py, params.json

## Rationale

The flat 5% trailing stop fails split-window robustness (second-half Sharpe -0.073). Every vol-scaled K value (10-40) passes both halves. Best at K=40: Sharpe 1.08 (+126% vs no-trail, +211% vs flat 5%), return 5.41%, win rate 44.7%. Mechanism: TRAIL_K scales the 5% base by the ticker's 20-day vol stdev, clamped to [4%, 12%]. Same intuition as the reverted stop_patience.py but anchored to realized volatility, not calendar time — noisy small-caps get room to breathe, stable names stay tight. Anchored in real observation: trailing stops are the dominant loss category (~29% win rate over 17 exits). Full evidence in research/2026-07-28.md.

## Evidence

replay_check.py --split-window: v1.0-trail (flat 5%) fails split-window, Sharpe -0.073 second half. v1.0-trail-vol passes both halves at default K=25. --sweep-trail with current live params: K=40 gives best split-window Sharpe (1.08) across all trail configs tested (K=10/20/30/40 + no-trail control). Full details in research/2026-07-28.md.

## Resolution

escalated at 2026-07-30T23:31:06.411835+00:00

## Resolution

applied at 2026-08-05T14:14:13.807430+00:00
