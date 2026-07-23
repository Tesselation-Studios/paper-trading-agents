# Skill: Rule Mechanization Audit

Run during nightly Step 3 (Evolve). Every guardrail added this week (NVDA trim, duplicate-order block, discovery merge, bankroll wiring) followed the same shape: a prose rule got flagged as violated in the journal 2+ times before anyone mechanized it. This skill formalizes catching that pattern early instead of by accident.

## Process

1. **List every prose rule currently governing behavior** — `strategy.md`'s "Current Approach" + any rule in "What I'm Learning" tagged "Codified vX.X". Skip anything already mechanically enforced (check `params.json`'s `guardrail_gates` block and `executor.py`'s `GATES` dict / `check_stops()` — if a rule is already a real gate, it's not prose anymore, don't re-flag it).

2. **Cross-reference each remaining rule against the last `synthesis.lookback_n_entries` journal entries.** For each one, classify:

| Class | Criteria | Action |
|---|---|---|
| ✅ Holding | No violations in lookback window | No action |
| ⚠️ Mechanize candidate | Violated/skipped/delayed 2+ times in lookback | Escalate (see below) — do not attempt the code yourself |
| 💤 Untested | Rule exists but never actually triggered in lookback | No action yet — not enough evidence either way |
| 🗑️ Removal candidate | Superseded by a later rule, references a dropped strategy version, or hasn't mattered across 10+ entries despite chances to | Propose removing in this cycle's Evolve step, same as any other strategy.md edit — version bump, rationale, git commit |

3. **For ⚠️ mechanize candidates**: do not write the Python yourself — a bad guardrail with real trading consequences is worse than a slow one. Instead:
   - Write the finding into this cycle's journal Evolve section: which rule, how many violations, dates, and (if obvious) which existing gate it's most similar to (e.g. "same shape as the NVDA position-size-trim fix — a threshold that's breached by drift, not by a new action").
   - Send one Telegram alert (`message(action=send, channel=telegram, target=8734159864, ...)`) summarizing the candidate — this is what actually gets it built, same path that worked for every fix this week. One alert per candidate per cycle, don't repeat across ticks.

4. **For 🗑️ removal candidates**: handle directly in this cycle's Evolve step like any other strategy.md change — remove the rule, bump version, explain why in Version History (what it was, why it's being dropped, what evidence supports it not mattering).

## Why escalate instead of self-mechanize

Every mechanical fix built this week required real engineering judgment beyond a single tick's budget — reading the exact call site, writing tests, verifying against live data before trusting it near real trades. That's Claude Code / a dedicated session's job, not a 30-minute nightly maintenance window. This skill's job is *noticing* reliably, not *building*.
