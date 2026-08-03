# Proposal: Extend replay_order.py with thesis-claim/invalidation for backtest validation

**Status**: applied
**Tier**: review_required
**Created**: 2026-08-03T05:45:23.186642+00:00
**Files changed**: scripts/replay_order.py

## Rationale

Discovered while running the chained-mode multi-day validation of the conviction-play thesis-persistence work (commit bc7778e): replay_order.py is a deliberately separate CLI from executor.py and had no equivalent of --thesis-claim/--thesis-invalidation at all, so the backtest harness couldn't exercise or persist the new mechanism -- the exact thing this validation exists to test. Mirrors executor.py's hard-required validation for --play-type long/conviction and wires thesis_claim/thesis_invalidation/thesis_entry_signals through to trader_db.upsert_position/log_thesis_event, same as the live path.

## Evidence

5 new tests in tests/test_replay_order.py (TestThesisPersistence), 21 total passing. Full suite: 985 passed, 1 pre-existing unrelated flake.

## Resolution

applied at 2026-08-03T05:45:32.073608+00:00
