# Skill: Decision Tree (`decision_heuristics.md`)

A fast-path cache of already-proven judgment, consulted before full reasoning in `tick_prompt.md` steps 6 and 8. Not a second source of truth (numbers live in `strategy.md`/`params.json`, this file points at them) and not a gate — a clean match is a strong prior Stan can still override with a stated reason. Full artifact/node-schema documentation lives in `decision_heuristics.md`'s own header; this skill covers the process around it — how a node comes to exist, gets sized, and gets retired.

When a candidate does NOT get a clean `Active` match (a `Watch`-tier match, an `insufficient_data` node, or nothing at all) and it's worth spending money to find out more, see `skills/research-escalation.md` — the companion piece for what happens next. `tick_prompt.md` step 8 persists the match outcome either way (`trader_write.py watchlist-mark-evaluated --tree-match`); that's the only link between the two, nothing here changes.

## A node can only restate proven judgment, never originate it

A candidate pattern can only be proposed as a tree node once it has *already separately* cleared `strategy.md`'s own promotion bar (real backtest evidence, Sharpe-positive split-window) or is already a mechanized `executor.py` gate. The tree never invents new judgment — see `v4-spec.md`'s original "no per-condition micro-branches or switchboards" design decision, which this respects by construction.

## Lifecycle

1. **Candidate identified** — same `tasks/pending.md` occurrence-tracking pattern already used for strategy.md candidates: 2-3 quality occurrences (evidence-quality-gated — a phantom/bad-data occurrence doesn't count), surfaced via `learning/weekly-*.md` or `skills/rule-mechanization-audit.md`'s nightly sweep.
2. **`Watch` tier** — a candidate can get a named, tracked entry in `decision_heuristics.md`'s `## Watch` section as soon as it's identified, even before it clears the occurrence bar. `Recommended action: NONE` — a match is a cue for extra scrutiny in full reasoning, never a shortcut. This makes recurring-but-unproven patterns visible without granting them authority.
3. **`Active` tier** — once the occurrence bar clears, promote to `## Active`. Starting conviction tier: `standard` for entry-type nodes, or the tier-inverted equivalent for `risk/restraint`/`risk/exit` nodes (see `decision_heuristics.md`'s header for what inversion means). Never starts at `high-conviction` just because the underlying strategy.md rule is well-proven — tier is earned by the node's *own* tracked hit rate, not inherited.
4. **Tier promotion to `high-conviction`** — requires `scripts/tree_scorecard.py` to report `status: "scored"` (n ≥ `params.json: decision_tree.tier_promotion.min_samples`, currently 10) with `hit_rate >= decision_tree.tier_promotion.hit_rate_bar_high_conviction` (currently 0.70 — higher than the pre-trade `combined_confidence >= 0.60` conviction_play anchor, because tier promotion grants real capital exposure post-hoc, not a pre-trade sanity check).
5. **Tier demotion** — a subsequent `tree_scorecard.py` run showing a `high-conviction` node's hit rate drop back below the bar is a *detection*, not an automatic edit (same posture as `rule-mechanization-audit.md`'s ⚠️-flagging) — still routed through step 6 below.
6. **Any status/tier change** — self-commit directly to `decision_heuristics.md` during nightly Evolve once the bar above clears (auto-tier, see below), citing the scorecard numbers (or a documented counterexample) inline in the node's `Evidence` field and in the journal entry, same evidence-citation discipline as `strategy.md`'s promote/revert history (e.g. commit `76e5059`'s v1.20 scale-in revert) — just without a separate proposal file or review step.

## Tagging convention — how a node's hit rate gets tracked

No code changes anywhere in `executor.py`/`record_decision.py` — `--features` already accepts and stores arbitrary JSON verbatim. When step 6 or step 8's fast-path check applies (or is explicitly overridden), include in the same `--features` JSON already passed to `record_decision.py reconcile`/the executor BUY-or-SELL call:

```json
{"tree_node": "<node-id>", "tree_action_taken": "followed"}
```

or `"tree_action_taken": "overridden"` if you deviated from the node's recommendation with a stated reason. Also cite `[tree:<node-id>]` in `active.md`/`--rationale` either way — that's what makes adoption independently verifiable by grepping `active.md`/`heartbeat_log.md`, separate from the DB-level tagging.

## Auto vs review_required tier for `decision_heuristics.md` itself

`decision_heuristics.md` **is** in `evolution_proposal.py`'s `AUTO_TIER_FILES` (2026-08-12, moved in ahead of the original ~90-day trial period below — deliberate call, not a default: paper money, git-tracked, twice-daily VM backups, and the review_required posture was directly blocking the exact failure this was meant to prevent — `deployment_pressure_override_v0` sat at 4 losing occurrences with a ready-to-paste node drafted and a Telegram alert sent, never actually committed, while the same-tick pattern kept recurring). A status/tier change self-commits during nightly Evolve exactly like a `strategy.md`/`params.json` change — version-equivalent note, evidence cited inline (occurrence dates or scorecard numbers), journal rationale, git commit+push — no `evolution_proposal.py create`/Telegram/human-review round trip.

**Being auto-tier does not relax the evidence bar** — it moves who enforces it. `evolution_proposal.py` only routes by filename; it cannot and does not check evidence quality. The Lifecycle bars above (2-3 quality occurrences for a Watch→Active promotion, `tree_scorecard.py` `scored`+hit-rate for a tier change) are still the actual gate, now self-applied by nightly Evolve at commit time instead of handed to a human reviewer to apply. A change that doesn't clear the bar shouldn't be committed, full stop — auto-tier means "no second reviewer," not "no bar."

A change that needs BOTH a `decision_heuristics.md` node AND a `scripts/*.py` edit (e.g. a brand-new mechanized gate the node references) is still `review_required` overall — `classify_tier()` requires every touched file to be in `AUTO_TIER_FILES`, so mixing in code doesn't inherit the auto path.

Old posture, for history: `decision_heuristics.md` was `review_required` from 2026-08-10 (this infrastructure's introduction) to 2026-08-12, with a stated revisit trigger of ~90 days or 5-10 rubber-stamp review cycles. That trial was cut short by explicit decision rather than run to term — see the commit/journal entry from 2026-08-12 if the reasoning needs revisiting later.

The tier-*promotion thresholds* in `params.json: decision_tree.tier_promotion` are ordinary `auto`-tier numbers, tunable like any other params.json value — only node *content* and per-node tier *assignments* need review.

## Verification

- Grep `active.md`/`heartbeat_log.md` for `[tree:` citations whenever a covered situation recurs; cross-check against prose that independently mentions the same pattern. Citations missing where they're expected means the `tick_prompt.md` instruction isn't landing — escalate/rephrase, don't assume the tree is simply unused.
- `python3 scripts/tree_scorecard.py --dry-run` should run cleanly and report `insufficient_data` at low sample counts — expected, not a bug.
- Weekly review should track: tree-node citation rate, `Watch`-tier occurrence counts (especially any high-stakes ones), and any node crossing a tier boundary since last review.
