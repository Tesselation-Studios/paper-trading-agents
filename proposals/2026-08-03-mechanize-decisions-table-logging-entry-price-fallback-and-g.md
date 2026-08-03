# Proposal: Mechanize decisions-table logging, entry_price fallback, and gate_status surfacing

**Status**: applied
**Tier**: review_required
**Created**: 2026-08-03T02:45:25.535104+00:00
**Files changed**: scripts/executor.py, scripts/record_decision.py, tick_prompt.md, skills/tool-invocation.md, skills/self-improving-agent.md

## Rationale

Fixes two bugs surfaced by tonight's manually-triggered stonks-journal-watch run: (1) self-stats pipeline gap -- decisions table had 34 rows all before 2026-07-31 despite 61 real trades in experience.json, because the only writer was the standalone record_decision.py CLI, a manual second step tick_prompt.md asked the agent to remember after every BUY/SELL. Mechanized into executor.py (_record_decision_row), same hook-point pattern already proven for training_examples (record_entry_example, 2026-08-01) and experience.json (2026-07-27) -- same root cause, same fix shape. Also fixed a silent sub-gap: SELL-path entry_price lookup only checked live Alpaca positions, silently skipping ALL outcome bookkeeping (bankroll/experience/decisions/training_examples) when that lookup missed (27 of 61 total_trades sat unclassified) -- added a local trader_db fallback plus a loud stderr warning when both sources miss, correctness boundary unchanged. (2) Ghost-constraint pattern -- FLXS gated 'Consumer Cyclical FULL' for 29 consecutive ticks on a stale remembered 2/2 sector cap, hours after params.json's real cap had been raised to 5 (journal/2026-07-31.md). The gates themselves were already correct (read params.json fresh every call) -- the agent just never consulted them before self-excluding. Folded a gate_status block into the executor's always-called status action (same zero-extra-cost pattern as the existing deployment_pressure block) so fresh sector/order-count/position-count numbers are visible every tick regardless of what gets decided next, instead of a new subcommand the agent would still have to remember to call.

## Evidence

journal-watch cron run 2026-08-02 (self-stats gap: 61 trades logged, 0-2 shown, 27 unclassified); journal/2026-07-31.md FLXS 29-tick sector-cap incident; decisions table query confirmed 34 rows all pre-2026-07-31T18:19:45Z despite ongoing real trades; 18 new automated tests added (tests/test_executor_audit.py TestDecisionLogging + TestSellEntryPriceFallback, new tests/test_executor_status.py, tests/test_record_decision.py standalone regression test), all passing; full suite 932/933 passing (1 pre-existing unrelated flaky failure, confirmed via git-stash baseline check); verified signal_scorecard.py's fetch_labeled_training_examples filters on label_win IS NOT NULL so the new SELL-path exit-type training_examples row can never pollute hit-rate scoring. Approved live by Raf in this Claude Code session.

## Resolution

applied at 2026-08-03T02:45:34.668259+00:00
