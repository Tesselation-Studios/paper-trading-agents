# Proposal: Implement max_portfolio_risk_pct gate

**Status**: open
**Tier**: review_required
**Created**: 2026-07-23T13:27:53.253776+00:00
**Files changed**: scripts/executor.py

## Rationale

params.json.risk.max_portfolio_risk_pct (8.0) has existed since the guardrail engine shipped 2026-07-20 but was never mechanically enforced anywhere -- found dead by workspace_review.py's dead-param check 2026-07-23. Unlike order_count_audit_threshold_daily (a simple counter, implemented directly) and the alpaca.base_url/order_type bugs (simple config wiring, fixed directly), this needs a real design decision before any code gets written: what does portfolio risk actually mean here -- aggregate stop-loss-distance-weighted exposure across all open positions? Sum of (position_value * stop_loss_pct) / total_equity? Something else? Guessing at a risk formula and shipping it is exactly the failure mode skills/rule-mechanization-audit.md exists to prevent (a bad guardrail is worse than a slow one).

## Evidence

workspace_review.py dead-param scan, 2026-07-23: params.json.risk.max_portfolio_risk_pct not referenced in scripts/*.py or any live-consumed doc.
