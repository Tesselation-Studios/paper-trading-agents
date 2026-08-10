#!/usr/bin/env python3
"""Empirical per-node hit rate for decision_heuristics.md's fast-path tree,
from local trader_db.py's training_examples — sibling to signal_scorecard.py,
same MIN_SAMPLES/insufficient_data contract, but scores a different shape of
tag: a tree-node match isn't a directional (bullish/bearish) call the way a
signal is, it's "this proven pattern fired." Kept as its own script rather
than a flag on signal_scorecard.py so the two tag namespaces never mix.

Tagging convention (tick_prompt.md steps 6/8): a decision the tree influenced
carries `"tree_node": "<node-id>"` and `"tree_action_taken": "followed"|
"overridden"` in the same --features JSON already passed to
record_decision.py/executor.py — no code changes needed there, --features
already accepts and stores arbitrary JSON verbatim.

Hit definition (applies uniformly across node types -- see the note below on
why this is a deliberate simplification, not an oversight):
    followed   + label_win=True   -> hit   (applied the guidance, it worked)
    followed   + label_win=False  -> miss
    overridden + label_win=False  -> hit   (ignored a restraint/exit call,
                                             it would have been right)
    overridden + label_win=True   -> miss  (ignored it, worked out anyway)

This is exactly right for risk/restraint and risk/exit nodes (the strongest-
evidenced v1 nodes are both this type) — "overridden" there means proceeding
despite a caution flag, so a loss vindicates the node. For entry nodes
(conviction_play_anchor_v1, catalyst_led_entry_v1), "overridden" typically
means declining to act on the match at all, in which case there's no real
trade to attach a label_win to — those rows self-exclude in practice rather
than needing special-cased logic here. If a future entry node's "overridden"
case DOES produce a labeled counterfactual trade, this formula would score
it backwards for that specific row; catch it during the node's next
evolution_proposal.py review (decision_heuristics.md is review_required,
never auto-applied) rather than trying to hand a generic scorer node-type
awareness it doesn't have access to (node Type lives in decision_heuristics.md
prose, not the tagged training_examples row).

Usage:
    python3 scripts/tree_scorecard.py                # compute + write state/tree_scorecard.json
    python3 scripts/tree_scorecard.py --min-samples 10
    python3 scripts/tree_scorecard.py --dry-run       # print only, don't write
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402

log = logging.getLogger("tree_scorecard")

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = WORKSPACE_DIR / "state" / "tree_scorecard.json"

MIN_SAMPLES = 10


def fetch_tagged_examples(db_path: Path = None, label_horizon: str = "trade_close") -> list[dict]:
    """label_horizon defaults to 'trade_close', same reasoning as
    signal_scorecard.py -- a long play's predicted_by_date resolution is a
    different kind of label and would distort this scorecard if mixed in."""
    try:
        conn = trader_db.get_conn(db_path)
    except Exception as e:
        log.error("fetch_tagged_examples: DB unavailable: %s", e)
        return []

    try:
        rows = trader_db.fetch_labeled_training_examples(conn, label_horizon=label_horizon)
    except Exception as e:
        log.error("fetch_tagged_examples: query failed: %s", e)
        return []
    finally:
        conn.close()

    examples = []
    for r in rows:
        try:
            features = json.loads(r["features"]) if r["features"] else {}
        except (json.JSONDecodeError, TypeError):
            log.warning("fetch_tagged_examples: skipping row with malformed features JSON")
            continue
        if not isinstance(features, dict) or "tree_node" not in features:
            continue
        examples.append({
            "tree_node": features.get("tree_node"),
            "tree_action_taken": features.get("tree_action_taken"),
            "label_win": r["label_win"],
        })
    return examples


def score_tree_nodes(examples: list[dict], min_samples: int) -> dict:
    tally: dict[str, dict] = {}

    for ex in examples:
        node_id = ex.get("tree_node")
        action = ex.get("tree_action_taken")
        label_win = ex.get("label_win")
        if not node_id or action not in ("followed", "overridden"):
            continue

        entry = tally.setdefault(node_id, {"hits": 0, "misses": 0, "followed": 0, "overridden": 0})
        entry[action] += 1

        hit = (action == "followed" and label_win) or (action == "overridden" and not label_win)
        entry["hits" if hit else "misses"] += 1

    scorecard = {}
    for node_id, entry in tally.items():
        n = entry["hits"] + entry["misses"]
        if n < min_samples:
            scorecard[node_id] = {
                "n": n, "followed": entry["followed"], "overridden": entry["overridden"],
                "status": "insufficient_data",
                "min_samples_needed": min_samples,
            }
        else:
            scorecard[node_id] = {
                "n": n, "followed": entry["followed"], "overridden": entry["overridden"],
                "status": "scored",
                "hit_rate": round(entry["hits"] / n, 4),
            }
    return scorecard


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db-path", default=None, help="Override state/trader.db (dry-run/tests)")
    parser.add_argument("--label-horizon", default="trade_close",
                         choices=["trade_close", "long_play_prediction"])
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    examples = fetch_tagged_examples(db_path=db_path, label_horizon=args.label_horizon)
    scorecard = score_tree_nodes(examples, args.min_samples)

    output = {
        "label_horizon": args.label_horizon,
        "tagged_examples_total": len(examples),
        "min_samples_threshold": args.min_samples,
        "nodes": scorecard,
    }

    print(json.dumps(output, indent=2))

    if not args.dry_run:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
