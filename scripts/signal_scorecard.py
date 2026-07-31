#!/usr/bin/env python3
"""Empirical per-signal hit rate from training_examples (local trader_db.py,
migrated 2026-07-28 from remote Postgres -- same query semantics, just no
longer trader_id-scoped since trader_db.py is single-tenant).

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
import logging
import sys
from pathlib import Path

import trader_db

log = logging.getLogger("signal_scorecard")

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = WORKSPACE_DIR / "state" / "signal_scorecard.json"

MIN_SAMPLES = 10


def fetch_labeled_examples(trader_id: str, db_path: Path = None, label_horizon: str = "trade_close") -> list[dict]:
    """trader_id kept for CLI compatibility -- trader_db.py is single-tenant
    now, no longer filters by it. features comes back as a JSON string from
    SQLite (unlike the old Postgres JSONB, which psycopg2 auto-deserialized)
    -- parsed here so score_signals() keeps getting dicts, same contract as
    before the migration. A row with malformed JSON is skipped, not raised.

    label_horizon defaults to 'trade_close' (2026-07-30) -- a long play's
    predicted_by_date resolution (label_horizon='long_play_prediction')
    is a different kind of label (was the prediction right, independent of
    whether the position was later sold) and would silently distort this
    per-signal hit-rate scorecard if mixed in. Pass label_horizon=
    'long_play_prediction' explicitly (--label-horizon on the CLI) to see
    that view instead."""
    try:
        conn = trader_db.get_conn(db_path)
    except Exception as e:
        log.error("fetch_labeled_examples(%s): DB unavailable: %s", trader_id, e)
        return []

    try:
        rows = trader_db.fetch_labeled_training_examples(conn, label_horizon=label_horizon)
    except Exception as e:
        log.error("fetch_labeled_examples(%s): query failed: %s", trader_id, e)
        return []
    finally:
        conn.close()

    examples = []
    for r in rows:
        try:
            features = json.loads(r["features"]) if r["features"] else {}
        except (json.JSONDecodeError, TypeError):
            log.warning("fetch_labeled_examples(%s): skipping row with malformed features JSON", trader_id)
            continue
        examples.append({"features": features, "label_win": r["label_win"]})
    return examples


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
    parser.add_argument("--db-path", default=None, help="Override state/trader.db (dry-run/tests)")
    parser.add_argument("--label-horizon", default="trade_close",
                         choices=["trade_close", "long_play_prediction"],
                         help="'trade_close' (default) scores real closed-trade outcomes. "
                              "'long_play_prediction' scores long-play predicted_by_date hit rate instead "
                              "(params.json risk.long_play) -- review this before ever raising horizon_days.")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    examples = fetch_labeled_examples(args.trader_id, db_path=db_path, label_horizon=args.label_horizon)
    scorecard = score_signals(examples, args.min_samples)

    output = {
        "trader_id": args.trader_id,
        "label_horizon": args.label_horizon,
        "labeled_examples_total": len(examples),
        "min_samples_threshold": args.min_samples,
        "signals": scorecard,
    }

    print(json.dumps(output, indent=2))

    if not args.dry_run:
        # 2026-07-30: label-horizon-scoped filename -- long_play_prediction
        # runs must not clobber the trade_close scorecard file (or vice
        # versa), they're different metrics reviewed for different reasons.
        out_path = OUTPUT_PATH if args.label_horizon == "trade_close" else (
            OUTPUT_PATH.parent / f"signal_scorecard.{args.label_horizon}.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
