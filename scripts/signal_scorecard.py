#!/usr/bin/env python3
"""Empirical per-signal hit rate from trading.training_examples.

Not a learned model — that needs far more labeled rows than exist yet
(11 as of 2026-07-23). This just tracks, per signal name (technical,
sentiment, macro, fundamentals, flow, insiders, regime, ...), how often
that signal's stated direction matched the eventual trade outcome —
so Stan (and Raf) can see which signals have actually been right so
far, without pretending there's enough data for real weight-learning.

A signal's direction is scored "correct" if:
  bullish + label_win=True   -> hit
  bullish + label_win=False  -> miss
  bearish + label_win=False  -> hit  (bearish call, trade lost -> right to be cautious)
  bearish + label_win=True   -> miss (bearish call, trade won anyway -> wrong)
  neutral                    -> excluded from hit_rate, counted separately

Signals with fewer than MIN_SAMPLES labeled rows are flagged
"insufficient_data" instead of a misleadingly precise hit_rate — same
"monitoring threshold, need N more" pattern already used for the
trailing-stop win-rate elsewhere in this codebase.

Usage:
    python3 scripts/signal_scorecard.py                # compute + write state/signal_scorecard.json
    python3 scripts/signal_scorecard.py --trader-id stonks --min-samples 10
    python3 scripts/signal_scorecard.py --dry-run       # print only, don't write
"""
import argparse
import json
import sys
from pathlib import Path

import db

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = WORKSPACE_DIR / "state" / "signal_scorecard.json"

MIN_SAMPLES = 10


def fetch_labeled_examples(trader_id: str) -> list[dict]:
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT features, label_win
               FROM trading.training_examples
               WHERE trader_id = %s AND label_win IS NOT NULL""",
            (trader_id,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [{"features": r[0], "label_win": r[1]} for r in rows]


def score_signals(examples: list[dict], min_samples: int) -> dict:
    tally: dict[str, dict] = {}

    for ex in examples:
        features = ex["features"] or {}
        label_win = ex["label_win"]
        if not isinstance(features, dict):
            continue

        for name, val in features.items():
            if not isinstance(val, dict) or "direction" not in val:
                continue
            direction = val.get("direction")

            entry = tally.setdefault(name, {"hits": 0, "misses": 0, "neutral": 0})
            if direction == "neutral":
                entry["neutral"] += 1
            elif direction == "bullish":
                entry["hits" if label_win else "misses"] += 1
            elif direction == "bearish":
                entry["hits" if not label_win else "misses"] += 1

    scorecard = {}
    for name, entry in tally.items():
        n = entry["hits"] + entry["misses"]
        if n < min_samples:
            scorecard[name] = {
                "n": n, "neutral": entry["neutral"],
                "status": "insufficient_data",
                "min_samples_needed": min_samples,
            }
        else:
            scorecard[name] = {
                "n": n, "neutral": entry["neutral"],
                "status": "scored",
                "hit_rate": round(entry["hits"] / n, 4),
            }
    return scorecard


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trader-id", default="stonks")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    examples = fetch_labeled_examples(args.trader_id)
    scorecard = score_signals(examples, args.min_samples)

    output = {
        "trader_id": args.trader_id,
        "labeled_examples_total": len(examples),
        "min_samples_threshold": args.min_samples,
        "signals": scorecard,
    }

    print(json.dumps(output, indent=2))

    if not args.dry_run:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(output, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
