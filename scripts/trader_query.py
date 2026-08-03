#!/usr/bin/env python3
"""
trader_query.py — CLI query tool fronting trader_db.py (2026-07-28).

Encapsulates every query Stan needs against state/trader.db behind named
subcommands so nothing ever has to write raw SQL. Same shape as this
repo's existing CLI-wrapper convention (record_decision.py, promote_
candidates.py): argparse subcommands, JSON output, --db-path override for
tests/dry-runs. See skills/trader-db.md for when to use which subcommand.

Usage:
    python3 scripts/trader_query.py positions
    python3 scripts/trader_query.py positions --ticker AAA
    python3 scripts/trader_query.py positions --ticker AAA --with-history
    python3 scripts/trader_query.py watchlist
    python3 scripts/trader_query.py watchlist --batch 6
    python3 scripts/trader_query.py bankroll
    python3 scripts/trader_query.py bankroll --history
    python3 scripts/trader_query.py decisions --ticker AAA --limit 10
    python3 scripts/trader_query.py training-examples --labeled-only
    python3 scripts/trader_query.py news --ticker AAA --hours 24
    python3 scripts/trader_query.py audit-log --limit 20
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_positions(args, conn) -> None:
    if args.ticker:
        row = trader_db.get_position(conn, args.ticker.upper())
        if not row:
            _print({"error": f"no position found for {args.ticker.upper()}"})
            return
        if args.with_history:
            row["thesis_log"] = trader_db.get_thesis_log(conn, args.ticker.upper())
        _print(row)
    else:
        _print(trader_db.get_open_positions(conn))


def cmd_watchlist(args, conn) -> None:
    if args.batch:
        _print(trader_db.get_watchlist_batch(conn, args.batch))
    else:
        _print(trader_db.get_watchlist_candidates(conn))


def cmd_bankroll(args, conn) -> None:
    if args.history:
        _print(trader_db.get_bankroll_history(conn, limit=args.limit))
    else:
        state = trader_db.get_bankroll_state(conn)
        _print(state or {"error": "no bankroll_state row yet"})


def cmd_decisions(args, conn) -> None:
    query = "SELECT * FROM decisions"
    params = []
    if args.ticker:
        query += " WHERE ticker = ?"
        params.append(args.ticker.upper())
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(query, params).fetchall()
    _print([dict(r) for r in rows])


def cmd_training_examples(args, conn) -> None:
    if args.labeled_only:
        _print(trader_db.fetch_labeled_training_examples(conn))
        return
    rows = conn.execute(
        "SELECT * FROM training_examples ORDER BY created_at DESC LIMIT ?", (args.limit,)
    ).fetchall()
    _print([dict(r) for r in rows])


def cmd_news(args, conn) -> None:
    if not args.ticker:
        _print({"error": "--ticker is required for news"})
        return
    _print(trader_db.recent_watchlist_articles(conn, [args.ticker.upper()], hours=args.hours))


def cmd_audit_log(args, conn) -> None:
    rows = conn.execute(
        "SELECT * FROM alpaca_audit_log ORDER BY timestamp DESC LIMIT ?", (args.limit,)
    ).fetchall()
    _print([dict(r) for r in rows])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", default=None, help="Override state/trader.db (dry-run/tests)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("positions", help="Open positions, or one ticker's position")
    p.add_argument("--ticker", default=None)
    p.add_argument("--with-history", action="store_true",
                    help="With --ticker, also include recent position_thesis_log rows "
                         "(thesis_claim/thesis_invalidation history + recheck verdicts)")

    p = sub.add_parser("watchlist", help="Current watchlist candidates")
    p.add_argument("--batch", type=int, default=None,
                    help="Return only the N least-recently-evaluated candidates (never-evaluated first), "
                         "for bounded per-tick evaluation -- see params.json watchlist.eval_batch_size")

    p = sub.add_parser("bankroll", help="Current bankroll state, or recent history")
    p.add_argument("--history", action="store_true")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("decisions", help="Recent decisions, optionally filtered by ticker")
    p.add_argument("--ticker", default=None)
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("training-examples", help="Recent training examples")
    p.add_argument("--labeled-only", action="store_true")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("news", help="Recent cached articles for a ticker")
    p.add_argument("--ticker", default=None)
    p.add_argument("--hours", type=int, default=24)

    p = sub.add_parser("audit-log", help="Recent Alpaca API audit rows")
    p.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None
    conn = trader_db.get_conn(db_path)
    try:
        {
            "positions": cmd_positions,
            "watchlist": cmd_watchlist,
            "bankroll": cmd_bankroll,
            "decisions": cmd_decisions,
            "training-examples": cmd_training_examples,
            "news": cmd_news,
            "audit-log": cmd_audit_log,
        }[args.command](args, conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
