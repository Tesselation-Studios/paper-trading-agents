# Pending Action Items

The carry-forward mechanism the 2026-07-26 weekly review called for: *"items flagged in nightly syntheses (NVDA trim, sentiment escalation, homework stack) don't get executed because the next session's tick agent never reads the prior synthesis... A carry-forward mechanism (tasks/pending.md or active.md section) would close it."*

This file exists so a proposal made in one reflection session (nightly-maintenance, nightly-learning, weekly-review, or an off-hours note) survives into the next one instead of rotting in a journal entry nobody re-reads. Keep it **short** — open items only, plain checklist, resolved items get removed (the reasoning/outcome belongs in the journal entry or commit that resolved it, not here).

**Read this at the start of every nightly-maintenance / nightly-learning / weekly-review run.** For each open item: either resolve it (make the change, remove the line), or re-affirm it's still worth doing (leave it, don't just silently re-copy it — if it's been open more than ~2 weeks with no action, that's itself worth a line in the journal: is it actually not worth doing?).

**Write a new item here** whenever a reflection session identifies something concrete worth doing that isn't being done in the same session — a code change to propose, a param to reconsider, an escalation that needs a human, a pattern worth testing next off-hours window. One line, dated, sourced. Not for routine "watch this ticker" notes — those belong in active.md/positions/*.md, this file is for process/strategy follow-ups.

Format: `- [ ] YYYY-MM-DD (source): description`

## Open

- [ ] 2026-08-03 (nightly-maintenance): Track watchlist-to-entry conversion rate by regime. Add a counter or field to `active.md`'s daily template — CHOPPY converted at ~2% Aug 3, 0% Aug 4. Need SUSTAINABLE comparison data (no SUSTAINABLE sessions in lookback).
- [ ] 2026-08-03 (tick-replay): CHOPPY index ≠ every name chops — evaluate whether the "CHOPPY suppresses all entries" rule should be softened to "CHOPPY suppresses broad sweep but individual names with independent confirmed setups can still enter." Needs more data across different CHOPPY days. Wiki synthesis created at `syntheses/choppy-regime-individual-name-entry-guidance.md`.
- [ ] 2026-08-02 (weekly-review): Peak entry timing rule — track RDDT as occurrence #1 of first-30-min entry failure. If a second comparable loss happens from early-session momentum-spike entries, harden into strategy.md as "no new entries first 30 min or higher bar (limit orders, wider stop)."
- [ ] 2026-08-02 (weekly-review): Exit discipline rule candidate — track occurrences of +3%+ intraday gains fading to negative/stop. AMD replay and RDDT as data points. 1 more before hardening.
- [ ] 2026-08-02 (weekly-review): v1.7-gentle scale-in cap proposal — Raf review required. Split-window Sharpe 2.407 vs v1.7 1.918. Filed under `proposals/`.
- [ ] 2026-08-02 (weekly-review): Small-cap vs large-cap universe evidence — accumulating from overnight optimization (3 rounds). Weekend discussion item for Raf. Not actionable unilaterally.
- [ ] 2026-08-02 (weekly-review): MACDh fallback investigation (from Jul 23 — now 12 days, approaching 2-week mark Aug 6). Never started. Still worth doing as insurance against blackout days. Not blocking. If untouched at Aug 6 nightly, journal whether it's actually worth doing or should be dropped.
- [ ] 2026-08-03 (claude-code-session): Test-isolation gap in tests/test_executor_audit.py — `isolated_db_and_env` fixture doesn't patch `executor.ORDER_LOCK_DIR`. Low-priority one-line fix.
- [ ] 2026-08-03 (claude-code-session): `trader_write.py position-update-thesis --verdict` doesn't accept a `--timestamp`/simulated-date override. One-line fix, replay-only impact.
- [ ] 2026-08-04 (nightly-learning): CLIR same-session re-entry tracking — CLIR stopped at -10% (9:55 AM, $4.11→$3.96) then re-entered 1sh at $4.00 (1:55 PM) on same M1 Core Burner thesis. Closed at $3.96, MACDh flipped bearish (-0.08). Track outcome Wednesday. If a second same-session re-entry after a stop occurs, harden into strategy.md.
- [ ] 2026-08-04 (nightly-learning): Alpaca paper API scale-in restrictions — BBSI, TRIP, UTMD return 403 Forbidden on scale-in buy orders. Documented in MEMORY.md. If it blocks a conviction-play or long-play scale-in, workaround investigation needed.
- [ ] 2026-08-04 (claude-code-session): SCALE_IN_MAX_MULTIPLE sweep — 1.0x beats 1.5x on split-window Sharpe (both halves) and drawdown, but 1.5x beats on aggregate return. Worth a proper split-window evidence-gathering pass (like the original proposal did) before deciding whether to go further to 1.0. Not acted on tonight. Raw sweep numbers in claude-code session.
