# Proposal: Daily position reconciliation (Alpaca vs trader_db) with critical-finding trading block

**Status**: applied
**Tier**: review_required
**Created**: 2026-08-03T05:30:45.643684+00:00
**Files changed**: scripts/reconcile_positions.py, scripts/workspace_review.py, skills/off-hours-research.md

## Rationale

tasks/pending.md flagged a real, unresolved operational gap: three named phantom-position incidents (STVN 2sh vs 3sh, KEX cost-basis phantom, DXCM phantom), all sharing the same root cause -- positions/*.md files are legacy agent-written Alpaca-sync boilerplate with no script keeping them honest, so bookkeeping can drift from broker reality exactly like the 'ghost constraint' pattern found 2026-07-31. Adds scripts/reconcile_positions.py (diffs live Alpaca positions against trader_db.positions, three finding types: phantom Alpaca position, phantom DB position, quantity mismatch) run daily via the existing stonks-off-hours cron. Deliberately NOT fail-open -- this is an integrity check, not a logging convenience. A critical finding (phantom Alpaca position, real money at risk untracked) writes state/reconciliation_status.json, which a new check_position_reconciliation() in workspace_review.py reads on every subsequent tick's --gate call, blocking trading via the existing state/.workspace_blocked sentinel until a human resolves it and a later clean run clears the finding -- reuses the same mechanism already blocking on other critical findings, avoiding a race against workspace_review.py's own --gate clearing logic. Plan approved live by Raf via Claude Code plan mode.

## Evidence

16 new tests in tests/test_reconcile_positions.py covering all three finding types, tolerance handling, status file round-trip, and full end-to-end integration with workspace_review.py's gate mechanism (block on phantom position, stay clear when clean). Ran for real against live Alpaca + production trader.db: zero mismatches found, confirming current state is clean. Full trader-stonks suite: 980 passed, 1 pre-existing unrelated flaky test.

## Resolution

applied at 2026-08-03T05:30:51.096171+00:00
