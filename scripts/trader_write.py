#!/usr/bin/env python3
"""
trader_write.py — write-capable CLI for state/trader.db (2026-07-28, Phase 6b).

trader_query.py is deliberately read-only (skills/trader-db.md documents
it that way, and that's a real property worth keeping — it can never
corrupt state no matter how it's invoked). This is the separate script
for the handful of writes that don't already happen automatically inside
another script's own flow (positions are opened/closed automatically by
executor.py's BUY/SELL, not through here — see trader_db.py directly).

Usage:
    python3 scripts/trader_write.py watchlist-add --ticker AAA --note "RSI 58, volume 1.2x"
    python3 scripts/trader_write.py watchlist-drop-stale
    python3 scripts/trader_write.py watchlist-drop-stale --threshold 24
    python3 scripts/trader_write.py watchlist-remove --ticker AAA
    python3 scripts/trader_write.py watchlist-mark-evaluated --tickers AAA,BBB,CCC
    python3 scripts/trader_write.py position-update-thesis --ticker AAA --thesis "..."
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
PARAMS_PATH = WORKSPACE_DIR / "params.json"

DEFAULT_IDLE_TICKS_BEFORE_DROP = 24


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _load_idle_ticks_threshold() -> int:
    try:
        params = json.loads(PARAMS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return DEFAULT_IDLE_TICKS_BEFORE_DROP
    return params.get("watchlist", {}).get("idle_ticks_before_drop", DEFAULT_IDLE_TICKS_BEFORE_DROP)


def cmd_watchlist_add(args, conn) -> None:
    """New candidate or touch (idle_ticks resets to 0). upsert_watchlist_candidate's
    ON CONFLICT overwrites price/rsi/volume_ratio/macd_hist unconditionally with
    whatever's passed -- a bare touch with no numeric flags would null out
    stored signal data unless we default unspecified flags to the existing
    row's values first."""
    ticker = args.ticker.upper()
    existing = next((c for c in trader_db.get_watchlist_candidates(conn) if c["ticker"] == ticker), None)

    def _default(flag_value, key):
        if flag_value is not None:
            return flag_value
        return existing[key] if existing else None

    trader_db.upsert_watchlist_candidate(
        conn, ticker=ticker,
        price=_default(args.price, "price"),
        rsi=_default(args.rsi, "rsi"),
        volume_ratio=_default(args.volume_ratio, "volume_ratio"),
        macd_hist=_default(args.macd_hist, "macd_hist"),
        source=args.source if args.source is not None else (existing["source"] if existing else None),
        note=args.note if args.note is not None else (existing["note"] if existing else None),
    )
    row = next(c for c in trader_db.get_watchlist_candidates(conn) if c["ticker"] == ticker)
    _print({"action": "touched" if existing else "added", "candidate": row})


def cmd_watchlist_drop_stale(args, conn) -> None:
    threshold = args.threshold if args.threshold is not None else _load_idle_ticks_threshold()
    dropped = trader_db.drop_stale_watchlist_candidates(conn, idle_ticks_threshold=threshold)
    _print({"threshold": threshold, "dropped": dropped})


def cmd_watchlist_remove(args, conn) -> None:
    ticker = args.ticker.upper()
    trader_db.remove_watchlist_candidate(conn, ticker)
    _print({"removed": ticker})


def cmd_watchlist_mark_evaluated(args, conn) -> None:
    """Bumps idle_ticks for every candidate NOT in --tickers (2026-07-28,
    pairs with trader_query.py watchlist --batch N). Evaluated names are left
    flat rather than reset to 0 -- they're not being re-discovered, just
    looked at -- so the ones climbing fastest are always whoever's gone
    longest without evaluation, giving a round-robin over several ticks with
    no separate cursor to track."""
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    trader_db.increment_idle_ticks(conn, except_tickers=tickers)
    _print({"evaluated_this_tick": tickers})


def cmd_position_update_thesis(args, conn) -> None:
    ticker = args.ticker.upper()
    existing = trader_db.get_position(conn, ticker)
    if not existing or existing["status"] != "open":
        _print({"error": f"no open position found for {ticker}"})
        sys.exit(1)
    trader_db.upsert_position(
        conn, ticker=ticker, shares=existing["shares"], entry_price=existing["entry_price"],
        entry_time=existing["entry_time"], sector=existing["sector"], thesis=args.thesis,
    )
    _print({"ticker": ticker, "thesis": args.thesis})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", default=None, help="Override state/trader.db (dry-run/tests)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("watchlist-add", help="Add a new candidate, or touch an existing one (resets idle_ticks)")
    p.add_argument("--ticker", required=True)
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--rsi", type=float, default=None)
    p.add_argument("--volume-ratio", type=float, default=None)
    p.add_argument("--macd-hist", type=float, default=None)
    p.add_argument("--source", default=None)
    p.add_argument("--note", default=None)

    p = sub.add_parser("watchlist-drop-stale", help="Drop candidates at/over the idle_ticks threshold")
    p.add_argument("--threshold", type=int, default=None, help="Default: params.json watchlist.idle_ticks_before_drop")

    p = sub.add_parser("watchlist-remove", help="Remove a candidate (e.g. promoted to a position)")
    p.add_argument("--ticker", required=True)

    p = sub.add_parser("watchlist-mark-evaluated",
                        help="Bump idle_ticks for every candidate except --tickers (call after a batch eval)")
    p.add_argument("--tickers", required=True, help="Comma-separated tickers evaluated this tick")

    p = sub.add_parser("position-update-thesis", help="Update an open position's thesis (not tied to a trade)")
    p.add_argument("--ticker", required=True)
    p.add_argument("--thesis", required=True)

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None
    conn = trader_db.get_conn(db_path)
    try:
        {
            "watchlist-add": cmd_watchlist_add,
            "watchlist-drop-stale": cmd_watchlist_drop_stale,
            "watchlist-remove": cmd_watchlist_remove,
            "watchlist-mark-evaluated": cmd_watchlist_mark_evaluated,
            "position-update-thesis": cmd_position_update_thesis,
        }[args.command](args, conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
