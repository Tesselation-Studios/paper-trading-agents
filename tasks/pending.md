# Pending Action Items

The carry-forward mechanism the 2026-07-26 weekly review called for: *"items flagged in nightly syntheses (NVDA trim, sentiment escalation, homework stack) don't get executed because the next session's tick agent never reads the prior synthesis... A carry-forward mechanism (tasks/pending.md or active.md section) would close it."*

This file exists so a proposal made in one reflection session (nightly-maintenance, nightly-learning, or an off-hours note) survives into the next one instead of rotting in a journal entry nobody re-reads. Keep it **short** — open items only, plain checklist, resolved items get removed (the reasoning/outcome belongs in the journal entry or commit that resolved it, not here).

**Read this at the start of every nightly-maintenance / nightly-learning / weekly-review run.** For each open item: either resolve it (make the change, remove the line), or re-affirm it's still worth doing (leave it, don't just silently re-copy it — if it's been open more than ~2 weeks with no action, that's itself worth a line in the journal: is it actually not worth doing?).

**Write a new item here** whenever a reflection session identifies something concrete worth doing that isn't being done in the same session — a code change to propose, a param to reconsider, an escalation that needs a human, a pattern worth testing next off-hours window. One line, dated, sourced. Not for routine "watch this ticker" notes — those belong in active.md/positions/*.md, this file is for process/strategy follow-ups.

Format: `- [ ] YYYY-MM-DD (source): description`

---

## 🔴 MONDAY PRE-FLIGHT (Aug 11, 2026)

Read and execute these before the first tick:

- [ ] Position DB reconciliation: `sqlite3 state/trader.db "SELECT * FROM positions WHERE symbol IN ('CLIR','MBBC');"` + Alpaca cross-ref. CLIR marked closed in DB but open in active.md at -7.14%. MBBC missing from DB entirely. 4 days stale.
- [ ] CLIR open price check: was $4.00 (entry $4.20), hard stop at $3.95 (-6%). If below $3.95, Alpaca stop already triggered — verify on Alpaca.
- [ ] Sell VSXY at Monday open: +10.70% (3sh, entry ~$89.39, now $98.98). Above 5% bootstrap trigger AND 10% profit target. Must execute.
- [ ] Discovery daemon health: `systemctl status stonks-discovery-daemon` — did it run during Aug 6-7 outage?
- [x] Index-anchor deployment: EXECUTED 1:02 PM ET Aug 10. SPY 1sh @ $772.80 conviction play, reconcile 0.6111 bullish/agree/signal_count:4. Protective stop $726.43. IWM scored 0.5833 (under 0.60 anchor, held). First index-anchor in system history. Mechanism live.

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
- [ ] 2026-08-09 (weekly-review): End-of-day position reconciliation — add automated check or explicit manual step to cross-reference active.md vs positions DB vs Alpaca account at close. Current gap (CLIR/MBBC) could recur.
- [ ] 2026-08-09 (weekly-review): Momentum re-screen — Aug 7 tick-replay surfaced process gap: names flagged as "interesting but not entering" aren't re-screened at subsequent intervals. VOYG +7.44% missed. Add re-screen step to tick workflow.
- [ ] 2026-08-10 (claude-code-session): CI's `tests/test_news_collector.py::TestScoreSentimentBatchViaWorker` (4 tests) fails in CI only — `from generated import gpu_compute_pb2` needs the GPU-compute gRPC client's protobuf-generated stubs, which live in a third sibling repo (`~/projects/gpu-compute`) only present on this machine, same class of issue the Aug 10 CI repair fixed elsewhere. NOT vendored (unlike replay.py/bar_loader.py/counterfactual.py) because this one is live protobuf/gRPC service-client code, not self-contained algorithmic logic — the code's own comments already warn a stale second copy of `generated/` silently wins some sys.path races. Needs a real decision (proper local package? git submodule? separately published?), not a quick copy.

---

## Resolved This Week (Aug 9 weekly review)

- [x] 2026-08-10 (claude-code-session): Pipeline null-price rot → ROOT CAUSE FOUND AND FIXED. Not a data-quotability gap (the earlier "add a quotability gate" proposal, now removed, would have wrongly suppressed real tickers) — `scripts/merge_discoveries.py`'s TICKER_HEADER_RE had no capture group for price, so every discoveries/*.md candidate landed in watchlist_candidates with price=NULL (confirmed 68-81% of the watchlist, verified against live Alpaca quotes: AORT/GAIN/CGBD/EPC/BWMN etc. were all real, liquid, quotable). Fixed the regex + extract_candidates(), backfilled today's 15 null rows, added a regression test.
- [x] 2026-08-07 (nightly-learning): MACDh fallback investigation → DROPPED. 5+ weeks reliable, total outage confirmed uselessness.
- [x] 2026-08-07 (nightly-maintenance): Scale-into-winners strategy.md note → RESOLVED. v1.20 already removed it.
- [x] 2026-08-02 (weekly-review): v1.7-gentle scale-in cap proposal → WITHDRAWN. v1.20 reverted scale-in entirely. Moot.
- [x] 2026-08-04 (claude-code-session): SCALE_IN_MAX_MULTIPLE sweep → WITHDRAWN. v1.20 reverted scale-in entirely. Moot.
- [x] 2026-08-07 (nightly-learning): API outage escalation → CONDITION CLEARED. Connectivity restored, no recurrence.