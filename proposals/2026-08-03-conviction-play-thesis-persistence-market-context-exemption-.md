# Proposal: Conviction-play thesis persistence + market-context exemption + daily re-confirmation

**Status**: applied
**Tier**: review_required
**Created**: 2026-08-03T05:21:06.852036+00:00
**Files changed**: scripts/trader_db.py, scripts/executor.py, scripts/trader_write.py, scripts/trader_query.py, tick_prompt.md

## Rationale

Raf's direction after a broader system review 2026-08-02/03: Stan should deploy cash slowly into high-conviction positions held for days/weeks through drawdown, matching his stated preference for higher absolute profit over win rate. Investigation found the conviction_play/long_play buckets were built and tested but never once used in practice (journal 2026-07-31: 'No conviction plays or long plays entered yet'), no rule told Stan when to reach for them, and the per-tick market-context judgment layer applied to every position with no exemption for a designated long-hold -- exactly what would kill a conviction play on ordinary noise. Also found thesis persistence was real but narrower than needed: positions.thesis/prediction_reason existed but as a single blob silently overwritten on every scale-in (no history), with no separation between 'why I believe this' and 'what would prove me wrong', and never proactively re-checked. This proposal: (1) extends trader_db.py with thesis_claim/thesis_invalidation/thesis_entry_signals + an append-only position_thesis_log table preserving history the old blob lost; (2) adds a numeric eligibility anchor for choosing conviction sizing (reconcile agreement + signal_count>=3 + combined_confidence>=0.60, validated via a historical scan of 28 past BUY decisions -- 5/28 would qualify, a reasonable selectivity, though too few labeled outcomes yet to statistically validate the threshold itself); (3) exempts conviction/long plays from the per-tick market-context exit layer, routing them to a new daily thesis re-confirmation instead (the conviction-gated redo of the stop_patience.py mechanism that was tried and reverted 2026-07-27 for lacking any conviction signal). Plan reviewed and approved live by Raf via Claude Code plan mode before implementation, following a Plan agent's detailed design informed by direct investigation of the actual current-state code.

## Evidence

trader_db.py: 11 new tests (TestThesisPersistence, TestPositionThesisLog, TestUpdateThesisCheck) in tests/test_trader_db.py, 78 total passing. executor.py: 6 new tests (TestThesisPersistenceCli) in tests/test_executor_audit.py covering hard-required validation, persistence, scale-in vs entry event logging, and fail-open behavior, 207 total passing in the executor/guardrails/status suite. trader_write.py/trader_query.py: new --verdict/--with-history tests, 35 passing. Full suite: 964 passed, 1 pre-existing unrelated flaky test (test_returns_matching_articles, confirmed failing identically before this session's changes). workspace_review.py: zero critical findings, same 4 pre-existing unrelated warnings before and after. Historical threshold scan: 5/28 past BUY decisions would have qualified for conviction eligibility under the 0.60/3-signal/agreement anchor.

## Resolution

applied at 2026-08-03T05:21:16.875564+00:00
