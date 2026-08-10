# Skill: Decision Tree (`decision_heuristics.md`)

A fast-path cache of already-proven judgment, consulted before full reasoning in `tick_prompt.md` steps 6 and 8. Not a second source of truth (numbers live in `strategy.md`/`params.json`, this file points at them) and not a gate — a clean match is a strong prior Stan can still override with a stated reason. Full artifact/node-schema documentation lives in `decision_heuristics.md`'s own header; this skill covers the process around it — how a node comes to exist, gets sized, and gets retired.

## A node can only restate proven judgment, never originate it

A candidate pattern can only be proposed as a tree node once it has *already separately* cleared `strategy.md`'s own promotion bar (real backtest evidence, Sharpe-positive split-window) or is already a mechanized `executor.py` gate. The tree never invents new judgment — see `v4-spec.md`'s original "no per-condition micro-branches or switchboards" design decision, which this respects by construction.

## Lifecycle

1. **Candidate identified** — same `tasks/pending.md` occurrence-tracking pattern already used for strategy.md candidates: 2-3 quality occurrences (evidence-quality-gated — a phantom/bad-data occurrence doesn't count), surfaced via `learning/weekly-*.md` or `skills/rule-mechanization-audit.md`'s nightly sweep.
2. **`Watch` tier** — a candidate can get a named, tracked entry in `decision_heuristics.md`'s `## Watch` section as soon as it's identified, even before it clears the occurrence bar. `Recommended action: NONE` — a match is a cue for extra scrutiny in full reasoning, never a shortcut. This makes recurring-but-unproven patterns visible without granting them authority.
3. **`Active` tier** — once the occurrence bar clears, promote to `## Active`. Starting conviction tier: `standard` for entry-type nodes, or the tier-inverted equivalent for `risk/restraint`/`risk/exit` nodes (see `decision_heuristics.md`'s header for what inversion means). Never starts at `high-conviction` just because the underlying strategy.md rule is well-proven — tier is earned by the node's *own* tracked hit rate, not inherited.
4. **Tier promotion to `high-conviction`** — requires `scripts/tree_scorecard.py` to report `status: "scored"` (n ≥ `params.json: decision_tree.tier_promotion.min_samples`, currently 10) with `hit_rate >= decision_tree.tier_promotion.hit_rate_bar_high_conviction` (currently 0.70 — higher than the pre-trade `combined_confidence >= 0.60` conviction_play anchor, because tier promotion grants real capital exposure post-hoc, not a pre-trade sanity check).
5. **Tier demotion** — a subsequent `tree_scorecard.py` run showing a `high-conviction` node's hit rate drop back below the bar is a *detection*, not an automatic edit (same posture as `rule-mechanization-audit.md`'s ⚠️-flagging) — still routed through step 6 below.
6. **Any status/tier change** — proposed via `evolution_proposal.py create --files decision_heuristics.md`, citing the scorecard numbers (or a documented counterexample) as `--evidence`, same template as `strategy.md`'s promote/revert history (e.g. commit `76e5059`'s v1.20 scale-in revert).

## Tagging convention — how a node's hit rate gets tracked

No code changes anywhere in `executor.py`/`record_decision.py` — `--features` already accepts and stores arbitrary JSON verbatim. When step 6 or step 8's fast-path check applies (or is explicitly overridden), include in the same `--features` JSON already passed to `record_decision.py reconcile`/the executor BUY-or-SELL call:

```json
{"tree_node": "<node-id>", "tree_action_taken": "followed"}
```

or `"tree_action_taken": "overridden"` if you deviated from the node's recommendation with a stated reason. Also cite `[tree:<node-id>]` in `active.md`/`--rationale` either way — that's what makes adoption independently verifiable by grepping `active.md`/`heartbeat_log.md`, separate from the DB-level tagging.

## Auto vs review_required tier for `decision_heuristics.md` itself

`decision_heuristics.md` is **not** in `evolution_proposal.py`'s `AUTO_TIER_FILES` — every status/tier change is `review_required`, same escalation path as a `scripts/*.py` change (`stonks-evolution-review` surfaces it to Raf/Claude-Code via Telegram, never self-clears). Reasoning: this is brand-new infrastructure with zero track record, and it now directly controls capital sizing via tier — a materially higher-stakes edit than a strategy.md prose tweak, and exactly the kind of compression-loses-nuance risk (e.g. `market_context_exit_v1`'s reactive-only-never-preemptive distinction) worth a human/Claude-Code pass before going live.

**Revisit trigger, stated explicitly**: after ~90 days, or 5-10 `review_required` cycles that are pure rubber-stamps with no substantive edits at review time, reconsider adding `decision_heuristics.md` to `AUTO_TIER_FILES` as its own deliberate follow-up proposal. Don't pre-decide it now.

The tier-*promotion thresholds* in `params.json: decision_tree.tier_promotion` are ordinary `auto`-tier numbers, tunable like any other params.json value — only node *content* and per-node tier *assignments* need review.

## Verification

- Grep `active.md`/`heartbeat_log.md` for `[tree:` citations whenever a covered situation recurs; cross-check against prose that independently mentions the same pattern. Citations missing where they're expected means the `tick_prompt.md` instruction isn't landing — escalate/rephrase, don't assume the tree is simply unused.
- `python3 scripts/tree_scorecard.py --dry-run` should run cleanly and report `insufficient_data` at low sample counts — expected, not a bug.
- Weekly review should track: tree-node citation rate, `Watch`-tier occurrence counts (especially any high-stakes ones), and any node crossing a tier boundary since last review.
