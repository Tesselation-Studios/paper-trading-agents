#!/usr/bin/env python3
"""
export_backtest_trades.py -- copies a backtest_session.py session's closed
trades into the LIVE trader.db's backtest_closed_trades table (2026-08-02),
so ml-trainer.service can learn from replay reps.

Deliberately NOT an export into `positions` or `training_examples`:
- `positions` has `ticker TEXT PRIMARY KEY` -- a backtest AAPL close would
  collide with a live AAPL row (open or already-closed). backtest_closed_trades
  is its own table precisely to avoid that.
- paper-trading-rebuild's ml_trainer_service.py's _stan_closed_trades()
  (the function that actually builds the real-trade half of the ML
  training set) reads closed `positions` rows directly -- it does NOT
  read `training_examples` at all. Exporting into training_examples
  would not have actually fed the trainer; backtest_closed_trades is the
  table its query was widened to UNION in.

Safe to run repeatedly for the same session (e.g. after every completed
day, or once at the end of a long chain) -- the live table's
(session_id, ticker, entry_time, closed_at) UNIQUE constraint makes a
re-export of an already-exported trade a no-op, not a duplicate or an
error.

Usage:
    python3 scripts/export_backtest_trades.py --session-id practice-chain-1
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest_session  # noqa: E402
import trader_db  # noqa: E402


def export_session(session_id: str) -> dict:
    session_db_path = backtest_session.resolve_session_db_path(session_id)

    session_conn = trader_db.get_conn(session_db_path)
    try:
        closed_trades = trader_db.get_closed_trades_for_export(session_conn)
    finally:
        session_conn.close()

    live_conn = trader_db.get_conn()
    try:
        exported = 0
        skipped = 0
        for trade in closed_trades:
            inserted = trader_db.export_closed_trade(
                live_conn, session_id=session_id,
                ticker=trade["ticker"], entry_time=trade["entry_time"], closed_at=trade["closed_at"],
                realized_pnl=trade["realized_pnl"], realized_return_pct=trade["realized_return_pct"],
                sector=trade["sector"], thesis=trade["thesis"], close_reason=trade["close_reason"],
            )
            if inserted:
                exported += 1
            else:
                skipped += 1
    finally:
        live_conn.close()

    return {
        "session_id": session_id,
        "total_closed_in_session": len(closed_trades),
        "exported": exported,
        "skipped_duplicate": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()

    try:
        result = export_session(args.session_id)
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e)}))
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
