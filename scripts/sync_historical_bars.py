#!/usr/bin/env python3
"""Sync daily 5-min bar history for Stan's actual current universe.

The engine repo has a real, working Alpaca-based backfill script
(paper-trading-rebuild/scripts/backfill_bars_alpaca.py) — idempotent,
only fetches missing dates, computes RSI/MACD/ATR. It just defaults to
a fixed old ticker list (mega-caps / old Kairos universe) that doesn't
overlap with Stan's actual rotating small-cap watchlist. This script
derives Stan's real current tickers (open positions + active watchlist
candidates) and calls the existing backfill script with that list.

Meant to run once daily, off-hours — see openclaw cron job
stonks-bars-sync. Small --days window since it's incremental/idempotent,
not a full history rebuild.

Usage:
    python3 scripts/sync_historical_bars.py
    python3 scripts/sync_historical_bars.py --dry-run
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

WORKSPACE_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402

ENGINE_REPO = Path("/home/openclaw/projects/paper-trading-rebuild")
BACKFILL_SCRIPT = ENGINE_REPO / "scripts" / "backfill_bars_alpaca.py"

load_dotenv(ENGINE_REPO / ".env")


# Always synced regardless of Stan's rotating universe — SPY bars feed
# get_market_regime()'s daily feature extraction (src/ml_signal.py), which
# needs today's RSI/MACD/volatility, not whatever was last fetched. The HMM
# model itself is retrained weekly (stonks-regime-retrain cron), but the
# bars it reads from at inference time need to stay fresh daily like this.
ALWAYS_SYNCED = ["SPY"]


def current_universe() -> list[str]:
    """Tickers actually relevant to Stan right now: open positions + active
    watchlist candidates. Migrated 2026-07-28 from globbing positions/*.md
    + regex-parsing watchlist.md to querying trader_db.py directly."""
    tickers = set(ALWAYS_SYNCED)

    conn = trader_db.get_conn()
    try:
        tickers |= {p["ticker"] for p in trader_db.get_open_positions(conn)}
        tickers |= {c["ticker"] for c in trader_db.get_watchlist_candidates(conn)}
    finally:
        conn.close()

    return sorted(tickers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=2,
                         help="Lookback window for the incremental backfill (default: 2)")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print the resolved ticker list and command, don't run it")
    args = parser.parse_args()

    tickers = current_universe()
    if not tickers:
        print("No tickers found (empty positions and watchlist candidates) — nothing to sync.")
        return 0

    ticker_arg = ",".join(tickers)
    cmd = [
        sys.executable, str(BACKFILL_SCRIPT),
        "--tickers", ticker_arg,
        "--days", str(args.days),
        "--verbose",
    ]

    print(f"Stan's current universe ({len(tickers)}): {ticker_arg}")
    if args.dry_run:
        print("DRY RUN — would run:", " ".join(cmd))
        return 0

    if not BACKFILL_SCRIPT.exists():
        print(f"ERROR: backfill script not found at {BACKFILL_SCRIPT}", file=sys.stderr)
        return 1

    # backfill_bars_alpaca.py wants generic APCA_API_KEY_ID/APCA_API_SECRET_KEY;
    # this repo's .env only has the per-account ALPACA_STONKS_KEY/_SECRET. Market
    # data isn't account-scoped, so reuse Stan's paper creds without touching the
    # shared backfill script.
    env = os.environ.copy()
    if not env.get("APCA_API_KEY_ID") and env.get("ALPACA_STONKS_KEY"):
        env["APCA_API_KEY_ID"] = env["ALPACA_STONKS_KEY"]
    if not env.get("APCA_API_SECRET_KEY") and env.get("ALPACA_STONKS_SECRET"):
        env["APCA_API_SECRET_KEY"] = env["ALPACA_STONKS_SECRET"]

    result = subprocess.run(cmd, cwd=str(ENGINE_REPO), env=env)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
