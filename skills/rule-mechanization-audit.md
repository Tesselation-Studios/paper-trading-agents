# Skill: Rule Mechanization Audit

Run during nightly Step 3 (Evolve). Every guardrail added this week (NVDA trim, duplicate-order block, discovery merge, bankroll wiring) followed the same shape: a prose rule got flagged as violated in the journal 2+ times before anyone mechanized it. This skill formalizes catching that pattern early instead of by accident.

## Process

1. **List every prose rule currently governing behavior** — `strategy.md`'s "Current Approach" + any rule in "What I'm Learning" tagged "Codified vX.X". Skip anything already mechanically enforced (check `params.json`'s `guardrail_gates` block and `executor.py`'s `GATES` dict / `check_stops()` — if a rule is already a real gate, it's not prose anymore, don't re-flag it).

2. **Cross-reference each remaining rule against the last `synthesis.lookback_n_entries` journal entries.** For each one, classify:

| Class | Criteria | Action |
|---|---|---|
| ✅ Holding | No violations in lookback window | No action |
| ⚠️ Mechanize candidate — tree fix | Violated/skipped/delayed 2+ times, and the fix is a `decision_heuristics.md` node (new or promoted) — no code changes needed | Self-mechanize now (see 3a) |
| ⚠️ Mechanize candidate — code fix | Same evidence bar, but the fix needs a `scripts/*.py`/guardrail change | Escalate (see 3b) — do not attempt the code yourself |
| 💤 Untested | Rule exists but never actually triggered in lookback | No action yet — not enough evidence either way |
| 🗑️ Removal candidate | Superseded by a later rule, references a dropped strategy version, or hasn't mattered across 10+ entries despite chances to | Propose removing in this cycle's Evolve step, same as any other strategy.md edit — version bump, rationale, git commit |

3a. **For ⚠️ tree-fix mechanize candidates** (2026-08-12: `decision_heuristics.md` is `evolution_proposal.py`'s `AUTO_TIER_FILES` now, see `skills/decision-tree.md`): write the node directly and self-commit in this cycle's Evolve step, same mechanics as a `strategy.md`/`params.json` edit — cite the occurrence dates/count inline in the node's `Evidence` field, explain the promotion in the journal Evolve section, git commit+push. This is the fix for the exact failure this skill exists to prevent: `deployment_pressure_override_v0` cleared 2+ occurrences, got a ready-to-paste node drafted, and a Telegram alert sent — three separate times — before it was ever actually committed, while a 4th occurrence (HPK) happened in the gap. Escalating a pure-node fix and waiting on a human/Claude-Code session to notice the alert is the failure mode, not the safeguard, for this file specifically.

3b. **For ⚠️ code-fix mechanize candidates**: do not write the Python yourself — a bad guardrail with real trading consequences is worse than a slow one, and this is genuinely a different risk shape than a tree node (a broken `scripts/*.py` gate can break the whole trading loop, not just one entry decision). Instead:
   - Write the finding into this cycle's journal Evolve section: which rule, how many violations, dates, and (if obvious) which existing gate it's most similar to (e.g. "same shape as the NVDA position-size-trim fix — a threshold that's breached by drift, not by a new action").
   - Send one Telegram alert (`message(action=send, channel=telegram, target=8734159864, ...)`) summarizing the candidate, AND create a workboard card for it (see `skills/evolution-proposals.md`'s workboard note) so it's tracked/claimable rather than resting on someone noticing the chat message. One alert + one card per candidate per cycle, don't repeat across ticks.

4. **For 🗑️ removal candidates**: handle directly in this cycle's Evolve step like any other strategy.md change — remove the rule, bump version, explain why in Version History (what it was, why it's being dropped, what evidence supports it not mattering). Same self-commit posture applies if the removal is from `decision_heuristics.md` (auto-tier) as from `strategy.md`.

## Why code fixes still escalate, and tree fixes no longer do

A `scripts/*.py` guardrail requires real engineering judgment beyond a single tick's budget — reading the exact call site, writing tests, verifying against live data before trusting it near real trades. That's still Claude Code / a dedicated session's job, not a 30-minute nightly maintenance window — this half of the skill's job stays *noticing* reliably, not *building*. A `decision_heuristics.md` node is structurally different: it's prompt content the LLM reads and reasons over each tick, not compiled/executed code, it already has its own evidence pipeline (`tree_scorecard.py`), and Stan has already shown he can draft correct node content unassisted (see the `deployment_pressure_override_v0` case above) — the missing piece was never capability, it was a human/Claude-Code session in the loop to press commit. Given this is paper trading (git-tracked, twice-daily VM backups), that review step was pure latency with no real safety benefit for this file, so it's gone.

## Also: `decision_heuristics.md` drift check

Each `Active`-tier node in `decision_heuristics.md` (see `skills/decision-tree.md` for the full lifecycle) cites an `Origin` — usually a specific `strategy.md` rule/version it restates. As part of this same nightly sweep, check whether that cited rule still reads the way the node's `Trigger`/`Recommended action` fields assume. If `strategy.md` changed (a version bump, a reworded rule) without the corresponding node being updated in the same edit, that's drift — fix it directly in this cycle's Evolve step (same self-commit posture as 3a above, decision_heuristics.md is auto-tier), noting in the journal which node, what changed in strategy.md, and what the node said before.
