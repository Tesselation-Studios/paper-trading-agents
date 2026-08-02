# Pending Action Items

The carry-forward mechanism the 2026-07-26 weekly review called for: *"items flagged in nightly syntheses (NVDA trim, sentiment escalation, homework stack) don't get executed because the next session's tick agent never reads the prior synthesis... A carry-forward mechanism (tasks/pending.md or active.md section) would close it."*

This file exists so a proposal made in one reflection session (nightly-maintenance, nightly-learning, weekly-review, or an off-hours note) survives into the next one instead of rotting in a journal entry nobody re-reads. Keep it **short** — open items only, plain checklist, resolved items get removed (the reasoning/outcome belongs in the journal entry or commit that resolved it, not here).

**Read this at the start of every nightly-maintenance / nightly-learning / weekly-review run.** For each open item: either resolve it (make the change, remove the line), or re-affirm it's still worth doing (leave it, don't just silently re-copy it — if it's been open more than ~2 weeks with no action, that's itself worth a line in the journal: is it actually not worth doing?).

**Write a new item here** whenever a reflection session identifies something concrete worth doing that isn't being done in the same session — a code change to propose, a param to reconsider, an escalation that needs a human, a pattern worth testing next off-hours window. One line, dated, sourced. Not for routine "watch this ticker" notes — those belong in active.md/positions/*.md, this file is for process/strategy follow-ups.

Format: `- [ ] YYYY-MM-DD (source): description`

## Open

- [ ] 2026-08-02 (weekly-review): Peak entry timing rule — track RDDT as occurrence #1 of first-30-min entry failure. If a second comparable loss happens from early-session momentum-spike entries, harden into strategy.md as "no new entries first 30 min or higher bar (limit orders, wider stop)."
- [ ] 2026-08-02 (weekly-review): Exit discipline rule candidate — track occurrences of +3%+ intraday gains fading to negative/stop. AMD replay and RDDT as data points. 1 more before hardening.
- [ ] 2026-08-02 (weekly-review): Daily position reconciliation — implement end-of-day cross-check (positions DB vs active.md vs Alpaca GET /positions). STVN (2sh vs 3sh), KEX cost-basis phantom, DXCM phantom all share this root cause.
- [ ] 2026-08-02 (weekly-review): Sentiment identity sync — acknowledge in SOUL.md that technicals are primary signal, sentiment is bonus layer. Documentation change only.
- [ ] 2026-08-02 (weekly-review): v1.7-gentle scale-in cap proposal — Raf review required. Split-window Sharpe 2.407 vs v1.7 1.918. Filed under `proposals/`.
- [ ] 2026-08-02 (weekly-review): Small-cap vs large-cap universe evidence — accumulating from overnight optimization (3 rounds). Weekend discussion item for Raf. Not actionable unilaterally.
- [ ] 2026-08-02 (weekly-review): Parallel tick collision verification — need 1 more clean session (Mon) to graduate to "resolved." `order_idempotency: true` in params.json is the suspected fix.
- [ ] 2026-08-02 (weekly-review): MACDh signal graduation — update MEMORY.md to reflect 5+ weeks validated as foundation signal. No active tracking needed. (Done in this session.)
- [ ] 2026-08-02 (weekly-review): MACDh fallback investigation (from Jul 23 — 2+ weeks open). Never started. Still worth doing as insurance against blackout days. Not blocking.
- [ ] 2026-08-02 (weekly-review): Peak-entry discipline experiment — 3-day wrap-up: rule saved $2.74 on 1 of 3 days, zero false negatives. Low priority but experiment completed; results should inform any formal peak-entry rule.
