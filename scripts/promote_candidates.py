#!/usr/bin/env python3
"""
promote_candidates.py — pool-to-watchlist bridge for the continuous
discovery daemon, added 2026-07-27.

Called unconditionally every tick from tick_prompt.md step 7, right after
the existing merge_discoveries.py call. Pulls the top fresh (in-band, not
stale) candidates out of discovery_daemon.py's SQLite pool
(state/discovery_pool.db) and merges them into strategies/watchlist.md via
merge_discoveries.insert_into_watchlist() — same dedup/max_size/format
contract merge_discoveries.py already uses for the discoveries/*.md path,
just fed from the pool instead. Idempotent, no-op if the pool has nothing
new or fresh enough.

This is the ONLY thing that reads discovery_daemon.py's pool — the daemon
itself never touches watchlist.md or discoveries/*.md directly.

Usage:
    python3 scripts/promote_candidates.py
    python3 scripts/promote_candidates.py --dry-run
    python3 scripts/promote_candidates.py --db-path state/scratch.db --dry-run
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discovery_db  # noqa: E402
import merge_discoveries  # noqa: E402

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
PARAMS_PATH = WORKSPACE_DIR / "params.json"

DEFAULT_TOP_N = 5
DEFAULT_MAX_AGE_SECONDS = 10800  # 3h


def _load_config():
    try:
        params = json.loads(PARAMS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        params = {}
    block = params.get("discovery_daemon", {})
    return {
        "promote_top_n": block.get("promote_top_n", DEFAULT_TOP_N),
        "promote_max_age_seconds": block.get("promote_max_age_seconds", DEFAULT_MAX_AGE_SECONDS),
    }


def select_promotable(conn, top_n: int = DEFAULT_TOP_N, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
                       now: str = None) -> list:
    """Thin wrapper over discovery_db.get_top_candidates() -- pure query,
    testable against a seeded tmp_path db."""
    now = now or datetime.now(timezone.utc).isoformat()
    return discovery_db.get_top_candidates(conn, limit=top_n, max_age_seconds=max_age_seconds, now=now)


def promote(dry_run: bool = False, db_path: Path = None, top_n: int = None, max_age_seconds: int = None) -> dict:
    config = _load_config()
    top_n = top_n if top_n is not None else config["promote_top_n"]
    max_age_seconds = max_age_seconds if max_age_seconds is not None else config["promote_max_age_seconds"]

    conn = discovery_db.get_conn(db_path)
    generation = discovery_db.get_universe_generation(conn)
    candidates = select_promotable(conn, top_n=top_n, max_age_seconds=max_age_seconds)
    conn.close()

    if not candidates:
        return {"merged": [], "skipped": [], "pool_candidates_considered": 0}

    source_label = f"discovery_pool gen {generation}" if generation is not None else "discovery_pool"
    result = merge_discoveries.insert_into_watchlist(
        [c["ticker"] for c in candidates], source_label=source_label, dry_run=dry_run,
    )
    result["pool_candidates_considered"] = len(candidates)
    result["source"] = source_label
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db-path", default=None, help="Override state/discovery_pool.db (dry-run/tests)")
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--max-age-seconds", type=int, default=None)
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    result = promote(dry_run=args.dry_run, db_path=db_path, top_n=args.top_n, max_age_seconds=args.max_age_seconds)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
