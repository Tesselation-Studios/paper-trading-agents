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

- [ ] 2026-08-10 (nightly-learning): P&L zero-bug monitoring — 3 closed positions (VSXY/FLXS/CLIR) had 0.00 realized P&L that went undetected across multiple sessions. The SELL exit-price timeout fix (1s→5s, 65c44bc) addresses the cause, but not the detection gap. Add an automated check: after every close, verify realized_pnl != 0.00. If 0, flag and re-fetch from Alpaca. Low priority — only fires on a timeout recurrence.
- [ ] 2026-08-10 (nightly-learning): SPY index-anchor sizing constraint — at ~$10.4K portfolio, 1 SPY share = 7.4% (>6% cap). Can't hold SPY conviction until portfolio > $12.9K or SPY price drops below ~$625. Use IWM/QQQ/DIA for index-anchor deployment in the meantime (IWM at $301 fits). Not a bug to fix, just a constraint to document in the index-anchor playbook.

- [ ] 2026-08-03 (nightly-maintenance): Track watchlist-to-entry conversion rate by regime. CHOPPY converted at ~2% Aug 3, 0% Aug 4, 6 entries from 7 priced on Aug 5. Need SUSTAINABLE comparison data. ⚠️ No data Aug 6-7 (outage).
- [ ] 2026-08-03 (tick-replay): CHOPPY index ≠ every name chops — evaluate softening "CHOPPY suppresses all entries" to "CHOPPY suppresses broad sweep but individual names with independent confirmed setups can still enter." Needs more data.
- [ ] 2026-08-02 (weekly-review): Peak entry timing rule — track RDDT as occurrence #1. If a second comparable loss from early-session momentum-spike entries, harden into strategy.md.
- [ ] 2026-08-02 (weekly-review): Exit discipline rule — track +3%+ intraday gains fading to negative/stop. AMD, RDDT. BJDX (+6.1% phantom) NOT counted. Still need 1 more real occurrence before hardening.
- [ ] 2026-08-02 (weekly-review): Small-cap vs large-cap universe evidence — now VERY HIGH confidence per the Aug 9/10 overnight cycles (9+ rounds combined): core/small-cap universes return ~2.4x broad/large-cap (8.78% vs 3.60% avg return across runs). Still a weekend discussion item for Raf (universe composition, not something to change unilaterally), but the evidence bar this item was waiting on has clearly cleared.
- [ ] 2026-08-03 (claude-code-session): Test-isolation gap in tests/test_executor_audit.py. Low-priority one-line fix.
- [ ] 2026-08-03 (claude-code-session): `trader_write.py position-update-thesis --verdict` timestamp override. One-line fix, replay-only impact.
- [ ] 2026-08-04 (nightly-learning): CLIR same-session re-entry tracking — occurrence #1 (Aug 4 re-entry) concluded Aug 10: CLIR stopped out at -7.22%, the re-entry eventually lost. Track a second occurrence of the pattern before hardening a rule either way.
- [ ] 2026-08-05 (raf-direction): Avoid new micro-cap entries until Alpaca websocket connectivity. Needs concrete threshold (price? market cap?) per Raf.
- [ ] 2026-08-05 (nightly-maintenance): BJDX micro-cap data quality — phantom position-stream spikes on sub-$3 names. Track recurrence.
- [ ] 2026-08-09 (weekly-review): Momentum re-screen — Aug 7 tick-replay surfaced process gap: names flagged as "interesting but not entering" aren't re-screened at subsequent intervals. VOYG +7.44% missed. Add re-screen step to tick workflow.
- [ ] 2026-08-10 (raf-session): tick_prompt.md missing index-anchor deploy trigger — Aug 10 EVIDENCE: IWM and SPY both deployed today via decision_heuristics.md's index_anchor_entry_v1 fast-path. The mechanism IS firing from the tree node, even without a dedicated step-8 trigger. The question is now: does the tree node alone provide sufficient coverage, or was today's deployment unique because a Claude Code session (Raf) explicitly primed the system? Monitor next 2-3 CHOPPY sessions — if index-anchor deploys autonomously from the tree node without human priming, close this item.
- [ ] 2026-08-11 (research-triage): Signal-quality pre-filter in the discovery engine — the Aug 9/10 overnight cycle's run 7 found that removing all discovery-stage filters (ultra-wide RSI/vol/conviction gates) surfaced 10,011 raw signals and caught zero real trades, i.e. the raw discovery stream is mostly noise upstream of the entry gates. No multi-timeframe/directional-agreement pre-filter exists in `discovery_scan.py` today. This is a discovery-engine change, not an entry-gate change, so it doesn't conflict with the gestalt-judgment philosophy — worth scoping as a concrete next step.
- [ ] 2026-08-11 (research-triage): Close the loop on the overnight research's fixed-threshold recommendations (`price_above_ma`, RSI(7)/MACD(12,32) as binary gates) — confirmed these are constructs of the optimizer's *simplified simulated* strategy, not the live gestalt-based entry logic (the Aug 9 insights doc explicitly says so itself: "the optimizer's conviction score is a coarse proxy... it CAN'T test the live entry logic"). Note this in the journal so future overnight cycles stop re-flagging non-actionable recommendations as if they applied to live trading.
- [ ] 2026-08-11 (research-triage): 7-period RSI as an alternative signal, worth testing (not adopting) — recommended across both the Aug 9 and Aug 10 overnight cycles (run 5: 4.45% catch rate, 0% false positives on loose-conviction/short-RSI stonks). Live technical data only exposes `rsi_14` (`bar_loader.py`/`discovery_screen.py`) — there's no `rsi_7` field anywhere in the live pipeline, so this needs a data-bus-side change (paper-trading-rebuild computing and exposing a second RSI period) before it's even testable, not a params.json tweak. Track as an evidence-gated experiment per this system's usual bar (2-3 occurrences) before hardening, same as any other strategy.md candidate — don't wire it in off one overnight run.
- [ ] 2026-08-11 (research-triage): Zero tech-sector discovery pipeline coverage despite Technology being the best-performing sector (75% win rate, 6W/2L per signal_scorecard) — the Aug 10 overnight insights note "we have zero pipeline coverage there" for a tech MA-proximity setup that otherwise tested well (6.71% return, run 4). Root cause not yet investigated (universe/ticker list composition vs. an explicit filter) — worth a look before acting on the MA-proximity setup itself, since that's blocked on tech names actually appearing in the watchlist first.

---

## Resolved This Week (Aug 9 weekly review + Aug 10 live session)

- [x] 2026-08-10 (claude-code-session): MBBC liquidity gate item was stale — `gate_catalyst_liquidity` has actually been fully mechanized (GATES dict + tests) since 2026-08-05, this Aug 3 item just never got removed after. Confirmed no real gap. Note: this gate only blocks *new* illiquid catalyst entries — MBBC itself (already held, 1sh since Aug 5) stays stuck regardless; exiting an already-illiquid position is a different, currently-unsolved problem, not something this gate addresses.
- [x] 2026-08-10 (claude-code-session): CI's `TestScoreSentimentBatchViaWorker` (4 tests) → FIXED. Every CI run since the pipefail fix was red for this. Turned out the tests (and the real news_collector.py code path) only ever import `generated.gpu_compute_pb2` — pure protoc-generated message definitions (90 lines, depends only on `google.protobuf`), never the actual live gRPC client (`orchestrator/gpu_client.py`, which the tests mock out entirely). Vendored just the message file (`generated/gpu_compute_pb2.py`) + added `protobuf` to requirements.txt — the live-client staleness/drift risk this item originally flagged doesn't apply, since that code was never what these tests needed.
- [x] 2026-08-10 (claude-code-session): Pipeline null-price rot → ROOT CAUSE FOUND AND FIXED. `scripts/merge_discoveries.py`'s TICKER_HEADER_RE had no capture group for price. 68-81% watchlist silently rejected since Aug 3. Fixed regex + backfill + regression test.
- [x] 2026-08-10 (raf-session): check_stops() oversized-position trim bug → FIXED (c2aff0b). Conviction plays now correctly evaluated against their own 10% cap instead of flat 6%. SPY re-entered after fix.
- [x] 2026-08-10 (raf-session): reconcile_positions.py cron gap → FIXED. Script was never wired into off-hours prompt, silently never ran. Added as Step 1. MBBC/BL drift resolved manually.
- [x] 2026-08-07 (nightly-learning): MACDh fallback investigation → DROPPED. 5+ weeks reliable, total outage confirmed uselessness.
- [x] 2026-08-07 (nightly-maintenance): Scale-into-winners strategy.md note → RESOLVED. v1.20 already removed it.
- [x] 2026-08-02 (weekly-review): v1.7-gentle scale-in cap proposal → WITHDRAWN. v1.20 reverted scale-in entirely. Moot.
- [x] 2026-08-04 (claude-code-session): SCALE_IN_MAX_MULTIPLE sweep → WITHDRAWN. v1.20 reverted scale-in entirely. Moot.
- [x] 2026-08-07 (nightly-learning): API outage escalation → CONDITION CLEARED. Connectivity restored, no recurrence.