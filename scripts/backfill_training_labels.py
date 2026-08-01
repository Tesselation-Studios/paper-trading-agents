#!/usr/bin/env python3
"""One-off backfill: re-label training_examples for already-closed positions
using the 2026-08-01 entry/exit correlation fix (find_labelable_entry_row).

Before the fix, record_trade_close() labeled "the latest unlabeled
training_examples row of any type" -- on a SELL that's usually the SELL's
own decision-log row, not the original BUY row carrying the predictive
signals. This script re-runs the (now-correct) correlation for every
closed position on record, so historical trades get a chance at a correct
label instead of only trades closed after the fix landed.

Safe to re-run: record_trade_close() only ever labels a row it finds via
find_entry_training_example()/unlabeled_legacy_training_examples(), both of
which search for UNLABELED rows only -- an already-correctly-labeled row is
never touched twice.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import decisions
import trader_db


def main():
    conn = trader_db.get_conn()
    closed = conn.execute(
        "SELECT ticker, entry_time, realized_pnl, realized_return_pct "
        "FROM positions WHERE status='closed' ORDER BY entry_time"
    ).fetchall()
    conn.close()

    print(f"{len(closed)} closed positions on record.\n")

    labeled, already_or_no_match, errors = 0, 0, 0
    for row in closed:
        ticker = row["ticker"]
        entry_time = row["entry_time"]
        pnl = row["realized_pnl"]
        return_pct = row["realized_return_pct"]
        result = decisions.record_trade_close(
            trader_id="stonks", ticker=ticker, trade_id=None,
            pnl=pnl, return_pct=return_pct, position_entry_time=entry_time,
        )
        if result.get("labeled"):
            labeled += 1
            print(f"  LABELED   {ticker:6s} entry={entry_time}  "
                  f"row_id={result['training_example_id']}  "
                  f"win={'Y' if (pnl or 0) > 0 else 'N'} ({return_pct:.2f}%)")
        elif result.get("error"):
            already_or_no_match += 1
            print(f"  no match  {ticker:6s} entry={entry_time}  ({result['error']})")
        else:
            errors += 1
            print(f"  ERROR     {ticker:6s} entry={entry_time}  {result}")

    print(f"\nDone: {labeled} newly labeled, {already_or_no_match} no unlabeled "
          f"entry row found (already labeled / no matching row exists), {errors} errors.")


if __name__ == "__main__":
    main()
