# Pending Action Items

The carry-forward mechanism the 2026-07-26 weekly review called for: *"items flagged in nightly syntheses (NVDA trim, sentiment escalation, homework stack) don't get executed because the next session's tick agent never reads the prior synthesis... A carry-forward mechanism (tasks/pending.md or active.md section) would close it."*

This file exists so a proposal made in one reflection session (nightly-maintenance, nightly-learning, or an off-hours note) survives into the next one instead of rotting in a journal entry nobody re-reads. Keep it **short** — open items only, plain checklist, resolved items get removed (the reasoning/outcome belongs in the journal entry or commit that resolved it, not here).

**Read this at the start of every nightly-maintenance / nightly-learning / weekly-review run.** For each open item: either resolve it (make the change, remove the line), or re-affirm it's still worth doing (leave it, don't just silently re-copy it — if it's been open more than ~2 weeks with no action, that's itself worth a line in the journal: is it actually not worth doing?).

**Write a new item here** whenever a reflection session identifies something concrete worth doing that isn't being done in the same session — a code change to propose, a param to reconsider, an escalation that needs a human, a pattern worth testing next off-hours window. One line, dated, sourced. Not for routine "watch this ticker" notes — those belong in active.md/positions/*.md, this file is for process/strategy follow-ups.

Format: `- [ ] YYYY-MM-DD (source): description`

---

## 🔴 MONDAY PRE-FLIGHT (Aug 10, 2026) — ALL RESOLVED

- [x] Position DB reconciliation: CLIR correctly closed in DB (hard stop Aug 4) — not in active.md. MBBC phantom Alpaca position created in DB (1sh @ $15.30). BL qty fixed (1→2, entry $30.00). reconcile_positions.py now clean (0 critical, 0 warnings). Root cause: script never wired into off-hours cron — fixed by Raf.
- [x] CLIR open price check: closed Aug 4 at $3.96 (hard stop breach). Not in active.md. Resolved.
- [x] Sell VSXY at Monday open: executed earlier today (+10.80% realized, $27.45 gains).
- [x] Discovery daemon health: N/A — watchlist unblinded by merge_discoveries.py fix (Claude Code session).
- [x] Index-anchor deployment: EXECUTED 1:02 PM ET Aug 10. SPY 1sh @ $773.09 conviction play, reconcile 0.6111 bullish/agree/signal_count:4. Stop $726.70. Re-entered 1:18 PM after check_stops() conviction-cap bug fix (c2aff0b). IWM scored 0.5833, held. Springboard guidance from Raf: actively rotate out into individual positions.

---

## Open

- [ ] 2026-08-03 (nightly-maintenance): Track watchlist-to-entry conversion rate by regime. CHOPPY converted at ~2% Aug 3, 0% Aug 4, 6 entries from 7 priced on Aug 5. Need SUSTAINABLE comparison data. ⚠️ No data Aug 6-7 (outage).
- [ ] 2026-08-03 (tick-replay): CHOPPY index ≠ every name chops — evaluate softening "CHOPPY suppresses all entries" to "CHOPPY suppresses broad sweep but individual names with independent confirmed setups can still enter." Needs more data.
- [ ] 2026-08-02 (weekly-review): Peak entry timing rule — track RDDT as occurrence #1. If a second comparable loss from early-session momentum-spike entries, harden into strategy.md.
- [ ] 2026-08-02 (weekly-review): Exit discipline rule — track +3%+ intraday gains fading to negative/stop. AMD, RDDT. BJDX (+6.1% phantom) NOT counted. Still need 1 more real occurrence before hardening.
- [ ] 2026-08-02 (weekly-review): Small-cap vs large-cap universe evidence — accumulating from overnight optimization (3 rounds). Weekend discussion item for Raf. Not actionable unilaterally.
- [ ] 2026-08-03 (nightly-maintenance): MBBC liquidity gate — implement pre-entry daily dollar volume check in code for catalyst-led entries on sub-$500M names. < $50K/day avg = skip. Rule in strategy.md v1.18, not enforced in code.
- [ ] 2026-08-03 (claude-code-session): Test-isolation gap in tests/test_executor_audit.py. Low-priority one-line fix.
- [ ] 2026-08-03 (claude-code-session): `trader_write.py position-update-thesis --verdict` timestamp override. One-line fix, replay-only impact.
- [ ] 2026-08-04 (nightly-learning): CLIR same-session re-entry tracking — survived full CHOPPY Aug 5. Track second occurrence before hardening.
- [ ] 2026-08-05 (raf-direction): Avoid new micro-cap entries until Alpaca websocket connectivity. Needs concrete threshold (price? market cap?) per Raf.
- [ ] 2026-08-05 (nightly-maintenance): BJDX micro-cap data quality — phantom position-stream spikes on sub-$3 names. Track recurrence.
- [ ] 2026-08-09 (weekly-review): End-of-day position reconciliation — add automated check or explicit manual step to cross-reference active.md vs positions DB vs Alpaca account at close. Current gap (CLIR/MBBC) could recur. reconcile_positions.py now wired into off-hours cron (Raf fix) — verify it runs tonight.
- [ ] 2026-08-09 (weekly-review): Momentum re-screen — Aug 7 tick-replay surfaced process gap: names flagged as "interesting but not entering" aren't re-screened at subsequent intervals. VOYG +7.44% missed. Add re-screen step to tick workflow.
- [ ] 2026-08-10 (raf-session): tick_prompt.md missing index-anchor deploy trigger — Step 8 only mentions reallocation of existing anchors. No step says "if CHOPPY + cash >85% + no existing anchors, deploy per v1.19." This is why it never fired across 14+ sessions. Needs a proposal to add the trigger. Partially addressed by decision_heuristics.md's index_anchor_entry_v1 node (fast-path check in step 8) — re-evaluate whether that alone is sufficient or a harder cash-threshold trigger is still needed after a few more sessions' evidence.

---

## Resolved This Week (Aug 9 weekly review + Aug 10 live session)

- [x] 2026-08-10 (claude-code-session): CI's `TestScoreSentimentBatchViaWorker` (4 tests) → FIXED. Every CI run since the pipefail fix was red for this. Turned out the tests (and the real news_collector.py code path) only ever import `generated.gpu_compute_pb2` — pure protoc-generated message definitions (90 lines, depends only on `google.protobuf`), never the actual live gRPC client (`orchestrator/gpu_client.py`, which the tests mock out entirely). Vendored just the message file (`generated/gpu_compute_pb2.py`) + added `protobuf` to requirements.txt — the live-client staleness/drift risk this item originally flagged doesn't apply, since that code was never what these tests needed.
- [x] 2026-08-10 (claude-code-session): Pipeline null-price rot → ROOT CAUSE FOUND AND FIXED. `scripts/merge_discoveries.py`'s TICKER_HEADER_RE had no capture group for price. 68-81% watchlist silently rejected since Aug 3. Fixed regex + backfill + regression test.
- [x] 2026-08-10 (raf-session): check_stops() oversized-position trim bug → FIXED (c2aff0b). Conviction plays now correctly evaluated against their own 10% cap instead of flat 6%. SPY re-entered after fix.
- [x] 2026-08-10 (raf-session): reconcile_positions.py cron gap → FIXED. Script was never wired into off-hours prompt, silently never ran. Added as Step 1. MBBC/BL drift resolved manually.
- [x] 2026-08-07 (nightly-learning): MACDh fallback investigation → DROPPED. 5+ weeks reliable, total outage confirmed uselessness.
- [x] 2026-08-07 (nightly-maintenance): Scale-into-winners strategy.md note → RESOLVED. v1.20 already removed it.
- [x] 2026-08-02 (weekly-review): v1.7-gentle scale-in cap proposal → WITHDRAWN. v1.20 reverted scale-in entirely. Moot.
- [x] 2026-08-04 (claude-code-session): SCALE_IN_MAX_MULTIPLE sweep → WITHDRAWN. v1.20 reverted scale-in entirely. Moot.
- [x] 2026-08-07 (nightly-learning): API outage escalation → CONDITION CLEARED. Connectivity restored, no recurrence.