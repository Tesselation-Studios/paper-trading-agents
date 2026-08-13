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
    python3 scripts/trader_write.py watchlist-drop-stale --max-evaluations 12 --max-age-hours 48
    python3 scripts/trader_write.py watchlist-remove --ticker AAA
    python3 scripts/trader_write.py watchlist-mark-evaluated --tickers AAA,BBB,CCC
    python3 scripts/trader_write.py watchlist-mark-evaluated --tickers AAA,BBB --entry-signal-tickers AAA
    python3 scripts/trader_write.py position-update-thesis --ticker AAA --thesis "..."
    python3 scripts/trader_write.py position-update-thesis --ticker AAA --verdict weakening --note "RSI rolled over"
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
PARAMS_PATH = WORKSPACE_DIR / "params.json"

def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_watchlist_params() -> dict:
    try:
        params = json.loads(PARAMS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        params = {}
    return params.get("watchlist", {})


def _load_drop_thresholds() -> tuple[int, float, int]:
    """params.json watchlist.max_evaluations_before_drop /
    max_age_hours_before_drop / interest_min_score, falling back to
    trader_db's defaults.

    Replaces watchlist.idle_ticks_before_drop (2026-08-01): dropping on
    the same counter the evaluation rotation ordered by meant a candidate
    could only reach the front of the queue by first reaching the drop
    threshold, so unevaluated names were deleted before they were ever
    looked at. See trader_db.drop_stale_watchlist_candidates()."""
    block = _load_watchlist_params()
    return (
        block.get("max_evaluations_before_drop", trader_db.DEFAULT_MAX_EVALUATIONS_BEFORE_DROP),
        block.get("max_age_hours_before_drop", trader_db.DEFAULT_MAX_AGE_HOURS_BEFORE_DROP),
        block.get("interest_min_score"),
    )


def _load_interest_weights() -> dict:
    return _load_watchlist_params().get("interest_score_weights", trader_db.DEFAULT_INTEREST_SCORE_WEIGHTS)


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
    default_evals, default_hours, default_interest_min = _load_drop_thresholds()
    max_evaluations = args.max_evaluations if args.max_evaluations is not None else default_evals
    max_age_hours = args.max_age_hours if args.max_age_hours is not None else default_hours
    interest_min_score = (
        args.interest_min_score if args.interest_min_score is not None else default_interest_min
    )
    dropped = trader_db.drop_stale_watchlist_candidates(
        conn, max_evaluations=max_evaluations, max_age_hours=max_age_hours,
        interest_min_score=interest_min_score,
    )
    _print({
        "max_evaluations": max_evaluations, "max_age_hours": max_age_hours,
        "interest_min_score": interest_min_score, "dropped": dropped,
    })


def cmd_watchlist_remove(args, conn) -> None:
    ticker = args.ticker.upper()
    trader_db.remove_watchlist_candidate(conn, ticker)
    _print({"removed": ticker})


def cmd_watchlist_mark_evaluated(args, conn) -> None:
    """Stamps last_evaluated_at and bumps eval_count on --tickers, sending
    them to the back of get_watchlist_batch()'s queue (2026-07-28, pairs
    with trader_query.py watchlist --batch N).

    Used to only bump idle_ticks for everyone else and leave the evaluated
    names flat, which deadlocked the rotation -- see
    trader_db.get_watchlist_batch().

    --entry-signal-tickers (2026-08-13, subset of --tickers) marks which
    ones matched a decision_heuristics.md Active node this eval -- feeds
    interest_score's entry_signal_triggered weight, recomputed for every
    evaluated ticker as part of this same call."""
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    entry_signal_tickers = (
        [t.strip().upper() for t in args.entry_signal_tickers.split(",") if t.strip()]
        if args.entry_signal_tickers else []
    )
    trader_db.mark_candidates_evaluated(
        conn, tickers, entry_signal_tickers=entry_signal_tickers,
        interest_weights=_load_interest_weights(),
    )
    _print({"evaluated_this_tick": tickers, "entry_signal_tickers": entry_signal_tickers})


def cmd_position_update_thesis(args, conn) -> None:
    """--thesis alone still just edits the current blob (unchanged
    behavior). --verdict (2026-08-03, daily thesis re-confirmation, see
    tick_prompt.md step 6) appends a 'recheck' row to position_thesis_log
    instead -- it's a history event, not a current-state edit, so it
    doesn't touch positions.thesis/thesis_claim at all. Both may be passed
    together (e.g. updating the blob while also recording the recheck)."""
    ticker = args.ticker.upper()
    existing = trader_db.get_position(conn, ticker)
    if not existing or existing["status"] != "open":
        _print({"error": f"no open position found for {ticker}"})
        sys.exit(1)
    if not args.thesis and not args.verdict:
        _print({"error": "at least one of --thesis or --verdict is required"})
        sys.exit(1)

    result = {"ticker": ticker}
    if args.thesis:
        trader_db.upsert_position(
            conn, ticker=ticker, shares=existing["shares"], entry_price=existing["entry_price"],
            entry_time=existing["entry_time"], sector=existing["sector"], thesis=args.thesis,
        )
        result["thesis"] = args.thesis
    if args.verdict:
        trader_db.update_thesis_check(conn, ticker, checked_at=_now_iso())
        trader_db.log_thesis_event(
            conn, ticker=ticker, event_type="recheck", verdict=args.verdict, note=args.note,
        )
        result["verdict"] = args.verdict
        result["note"] = args.note
    _print(result)


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

    p = sub.add_parser("watchlist-drop-stale", help="Drop exhausted (too many evaluations) or too-old candidates")
    p.add_argument("--max-evaluations", type=int, default=None,
                    help="Default: params.json watchlist.max_evaluations_before_drop")
    p.add_argument("--max-age-hours", type=float, default=None,
                    help="Default: params.json watchlist.max_age_hours_before_drop")
    p.add_argument("--interest-min-score", type=int, default=None,
                    help="Default: params.json watchlist.interest_min_score")

    p = sub.add_parser("watchlist-remove", help="Remove a candidate (e.g. promoted to a position)")
    p.add_argument("--ticker", required=True)

    p = sub.add_parser("watchlist-mark-evaluated",
                        help="Stamp --tickers as evaluated, sending them to the back of the rotation "
                             "(call after a batch eval)")
    p.add_argument("--tickers", required=True, help="Comma-separated tickers evaluated this tick")
    p.add_argument("--entry-signal-tickers", default=None,
                    help="Comma-separated subset of --tickers that matched a decision_heuristics.md "
                         "Active node this eval (feeds interest_score)")

    p = sub.add_parser("position-update-thesis", help="Update an open position's thesis (not tied to a trade), "
                                                        "or record a daily thesis recheck verdict")
    p.add_argument("--ticker", required=True)
    p.add_argument("--thesis", default=None, help="Edit the current thesis blob (positions.thesis)")
    p.add_argument("--verdict", default=None, choices=["intact", "weakening", "broken"],
                    help="Record a daily recheck verdict -- appends to position_thesis_log and bumps "
                         "thesis_last_checked_at/thesis_check_count. Does not edit positions.thesis.")
    p.add_argument("--note", default=None, help="Optional context for --verdict (e.g. what changed)")

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
