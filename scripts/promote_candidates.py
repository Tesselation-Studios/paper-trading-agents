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
        "min_market_cap": params.get("universe", {}).get("min_market_cap"),
        "interest_min_score": params.get("watchlist", {}).get("interest_min_score"),
    }


def select_promotable(conn, top_n: int = DEFAULT_TOP_N, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
                       now: str = None, min_market_cap: float = None,
                       exclude: set = None) -> list:
    """Thin wrapper over discovery_db.get_top_candidates() -- pure query,
    testable against a seeded tmp_path db.

    min_market_cap (2026-08-13, params.json universe.min_market_cap) drops
    any candidate whose yfinance-enriched market_cap is below the floor --
    a candidate with market_cap still NULL (enrichment hasn't run/
    succeeded yet) passes through unfiltered, since this is a best-effort
    risk trim on top of the technical screen, not a hard data-completeness
    gate. Filtered post-query rather than in SQL: LIMIT already applied
    upstream, so a promotion cycle with sub-floor names in the top N can
    come back with fewer than top_n results -- acceptable since
    promote_candidates.py runs every tick, so it self-corrects next cycle
    rather than needing a backfill query here.

    exclude (2026-08-13): set of tickers already promoted to the watchlist.
    To compensate for skips, the query fetches up to top_n * 3 candidates
    and filters after the fact."""
    now = now or datetime.now(timezone.utc).isoformat()
    fetch_limit = top_n * 3 if exclude else top_n
    candidates = discovery_db.get_top_candidates(conn, limit=fetch_limit, max_age_seconds=max_age_seconds, now=now)
    if exclude:
        candidates = [c for c in candidates if c["ticker"] not in exclude]
    if min_market_cap is not None:
        candidates = [c for c in candidates if c.get("market_cap") is None or c["market_cap"] >= min_market_cap]
    return candidates[:top_n]


def promote(dry_run: bool = False, db_path: Path = None, top_n: int = None, max_age_seconds: int = None,
            now: str = None, min_market_cap: float = None, interest_min_score: int = None) -> dict:
    config = _load_config()
    top_n = top_n if top_n is not None else config["promote_top_n"]
    max_age_seconds = max_age_seconds if max_age_seconds is not None else config["promote_max_age_seconds"]
    min_market_cap = min_market_cap if min_market_cap is not None else config["min_market_cap"]
    interest_min_score = (
        interest_min_score if interest_min_score is not None else config["interest_min_score"]
    )

    # 2026-08-13: Drop stale watchlist candidates unconditionally before
    # promoting new ones from the pool. Without this, the watchlist fills
    # up at max_size and nothing new can land -- the tick prompt listed
    # watchlist-drop-stale as an optional 'light touch' that Stan skipped,
    # so stale candidates accumulated until the downstream flow stalled.
    # interest_min_score (2026-08-13) additionally fast-drops candidates
    # that have been evaluated a few times and are demonstrably
    # uninteresting -- see trader_db.drop_stale_watchlist_candidates().
    #
    # trader_db.get_conn() deliberately takes no db_path here (2026-08-13
    # fix) -- this function's own db_path param is the discovery POOL db
    # (see module docstring/test file comment), a different database from
    # trader.db. Passing it through opened/wrote to a trader-schema table
    # inside the pool db file instead of the real watchlist whenever a
    # caller passed an explicit db_path (every test, any dry-run against a
    # scratch pool) -- drop-stale silently no-op'd on the real watchlist in
    # exactly those cases. trader_db.DB_PATH is the only override point
    # (monkeypatched directly in tests), matching insert_into_watchlist()'s
    # existing convention below.
    trader_conn = None
    try:
        import trader_db
        trader_conn = trader_db.get_conn()
        dropped = trader_db.drop_stale_watchlist_candidates(
            trader_conn,
            max_evaluations=12,
            max_age_hours=48,
            interest_min_score=interest_min_score,
        )
    except Exception:
        dropped = []

    # Exclude tickers already in the watchlist so the same top-ranked
    # candidates don't get dedup-skipped every tick while 200+ lower-ranked
    # fresh candidates never get a look.
    existing = set()
    try:
        if trader_conn is not None:
            for r in trader_conn.execute("SELECT ticker FROM watchlist_candidates").fetchall():
                existing.add(r["ticker"])
    except Exception:
        pass  # best-effort -- without exclude, falls back to dedup
    finally:
        if trader_conn is not None:
            trader_conn.close()

    conn = discovery_db.get_conn(db_path)
    generation = discovery_db.get_universe_generation(conn)
    candidates = select_promotable(conn, top_n=top_n, max_age_seconds=max_age_seconds, now=now,
                                    min_market_cap=min_market_cap, exclude=existing)
    conn.close()

    if not candidates:
        return {"merged": [], "skipped": [], "pool_candidates_considered": 0}

    source_label = f"discovery_pool gen {generation}" if generation is not None else "discovery_pool"
    # Full dicts, not [c["ticker"] for c in candidates] -- the pool row
    # already carries price/rsi/volume_ratio/macd_hist/sentiment/
    # news_headline, and dropping them here was forcing every tick to
    # re-derive them per candidate inside its budget (2026-08-01 fix).
    result = merge_discoveries.insert_into_watchlist(
        candidates, source_label=source_label, dry_run=dry_run,
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
    parser.add_argument("--min-market-cap", type=float, default=None)
    parser.add_argument("--interest-min-score", type=int, default=None)
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    result = promote(dry_run=args.dry_run, db_path=db_path, top_n=args.top_n, max_age_seconds=args.max_age_seconds,
                      min_market_cap=args.min_market_cap, interest_min_score=args.interest_min_score)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
