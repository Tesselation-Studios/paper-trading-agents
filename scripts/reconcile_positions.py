#!/usr/bin/env python3
"""
reconcile_positions.py — daily integrity check between live Alpaca
positions and state/trader.db's positions table (2026-08-03).

Root cause of the STVN (2sh vs 3sh)/KEX/DXCM phantom-position incidents
(tasks/pending.md): positions/*.md files are legacy, agent-written Alpaca-
sync boilerplate -- no script writes them, so they can drift from ground
truth exactly like the "ghost constraint" pattern found 2026-07-31. The
real authoritative sources are trader_db.positions (internal bookkeeping)
and live Alpaca GET /v2/positions (broker truth); positions/*.md is a
derived view, never a reconciliation source, and isn't touched here.

Unlike everything else in this codebase, this is deliberately NOT
fail-open -- it's specifically an integrity check, and silently
swallowing a mismatch defeats the purpose. A critical finding (a phantom
Alpaca position DB doesn't know about -- real money at risk with no
bookkeeping) writes to state/reconciliation_status.json, which
workspace_review.py's check_position_reconciliation() reads on every
subsequent tick's --gate call and blocks trading via the existing
state/.workspace_blocked sentinel until a human resolves it and a later
clean run clears the finding. A quantity/cost-basis mismatch (the STVN
pattern) is a warning-tier finding -- surfaced, not blocking.

Run daily via the stonks-off-hours cron (20:00 ET) rather than a
dedicated cron -- matches tasks/pending.md's own "weekly-review"/
end-of-day framing and avoids proliferating cron count.

Usage:
    python3 scripts/reconcile_positions.py
    python3 scripts/reconcile_positions.py --account stonks
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor  # noqa: E402
import trader_db  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
STATUS_PATH = REPO_ROOT / "state" / "reconciliation_status.json"

# Tolerance for a "same position" share-count match -- fractional shares
# from partial fills can differ by a hair without being a real mismatch.
SHARE_TOLERANCE = 0.01


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def reconcile(account: str = "stonks") -> dict:
    """Returns {"critical": [...], "warnings": [...], "checked_at": ...}.
    Does NOT write the status file -- see main()."""
    alpaca_positions = executor.get_positions(account)
    alpaca_by_ticker = {}
    for p in alpaca_positions:
        ticker = str(p.get("symbol", "")).upper()
        if not ticker:
            continue
        try:
            qty = float(p.get("qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        alpaca_by_ticker[ticker] = qty

    conn = trader_db.get_conn()
    try:
        db_open = trader_db.get_open_positions(conn)
    finally:
        conn.close()
    db_by_ticker = {row["ticker"].upper(): row for row in db_open}

    critical = []
    warnings = []

    for ticker, qty in alpaca_by_ticker.items():
        if ticker not in db_by_ticker:
            critical.append(
                f"phantom Alpaca position: {ticker} ({qty} shares) held at the broker "
                f"but not tracked as open in trader_db -- real money at risk with no bookkeeping"
            )

    for ticker, row in db_by_ticker.items():
        if ticker not in alpaca_by_ticker:
            critical.append(
                f"phantom DB position: {ticker} ({row['shares']} shares) marked open in trader_db "
                f"but not held at the broker -- bookkeeping believes a position exists that doesn't"
            )

    for ticker in set(alpaca_by_ticker) & set(db_by_ticker):
        alpaca_qty = alpaca_by_ticker[ticker]
        db_qty = float(db_by_ticker[ticker]["shares"])
        if abs(alpaca_qty - db_qty) > SHARE_TOLERANCE:
            warnings.append(
                f"quantity mismatch: {ticker} Alpaca={alpaca_qty} trader_db={db_qty}"
            )

    return {"checked_at": _now_iso(), "critical": critical, "warnings": warnings}


def write_status(result: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(result, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--account", default="stonks", choices=["stonks"])
    args = parser.parse_args()

    result = reconcile(args.account)
    write_status(result)
    print(json.dumps(result, indent=2))
    return 1 if result["critical"] else 0


if __name__ == "__main__":
    sys.exit(main())
