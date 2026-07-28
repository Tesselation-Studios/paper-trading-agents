#!/usr/bin/env python3
"""
migrate_watchlist_positions_to_db.py — one-time seed migration (2026-07-28,
Phase 6f). Seeds trader_db.py's positions/watchlist_candidates tables from
the real, live positions/*.md + strategies/watchlist.md content.

Hand-transcribed from a direct read of both, not a generic regex parser --
the real watchlist.md's formatting is inconsistent enough (blank-line
grouping, a stale duplicate candidate entry for STVN that's already an
open position, an ITRI entry duplicated between "Currently Held" and
"Closed Positions") that a generic parser risks silently mis-transcribing
messy real data. Numeric shares/entry_price for open positions ARE read
live from positions/*.md's consistently-formatted "**Entry**: $X avg | N
shares" line, since that part is reliable and benefits from being current
at run time rather than a snapshot.

Run with --dry-run first, inspect the output, then for real. Verify via
trader_query.py positions/watchlist/positions --status closed before
deleting positions/*.md (never before -- see Phase 6f in the plan).

Usage:
    python3 scripts/migrate_watchlist_positions_to_db.py --dry-run
    python3 scripts/migrate_watchlist_positions_to_db.py
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
POSITIONS_DIR = WORKSPACE_DIR / "positions"

# Hand-transcribed 2026-07-28 from strategies/watchlist.md's "## Currently
# Held" section -- entry_time/sector/thesis aren't reliably parseable from
# positions/*.md (which never had real sector/thesis text -- see Phase 6c's
# research), so this is the richer source for that context. Ticker,
# shares, entry_price are NOT taken from here -- those come from a live
# read of positions/*.md below, since they're consistently formatted and
# benefit from being current, not a snapshot.
POSITION_CONTEXT = {
    "BFH": {"entry_time": "2026-07-28T00:00:00-04:00", "sector": "Financial/Consumer Credit",
            "thesis": "Evercore PT $120, sentiment +0.93"},
    "BFST": {"entry_time": "2026-07-24T10:05:00-04:00", "sector": None, "thesis": None},
    "BOX": {"entry_time": "2026-07-24T11:50:00-04:00", "sector": "Technology",
            "thesis": "scaled in 2026-07-27 11:40 and 2026-07-28 9:35"},
    "STVN": {"entry_time": "2026-07-28T09:30:00-04:00", "sector": "Healthcare", "thesis": None},
    "HLN": {"entry_time": "2026-07-28T09:30:00-04:00", "sector": "Consumer Defensive", "thesis": None},
}

ENTRY_RE = re.compile(r"\*\*Entry\*\*:\s*\$([\d.]+)\s*avg\s*\|\s*(\d+)\s*shares")

# Hand-transcribed 2026-07-28 from watchlist.md's "## Candidates" section --
# every active (non-struck-through) entry, excluding STVN (a stale
# duplicate -- STVN is already an open position, listed here too only
# because nothing ever removed it from Candidates when it was bought; the
# new dedup logic in merge_discoveries.py makes this specific
# inconsistency structurally impossible going forward).
CANDIDATES = [
    ("NDAA", 0, None, "$23.61, RSI 44.8 below band, MACDh near-zero, vol 11k shares thin. Disqualified."),
    ("LDRX", 0, "discovery_pool gen 2", None),
    ("JCTC", 0, "discovery_pool gen 2", None),
    ("ISHP", 0, "discovery_pool gen 2", None),
    ("TTEC", 0, "2026-07-28.md", None),
    ("MPAA", 0, "2026-07-28.md", None),
    ("ATKR", 0, "2026-07-28.md", None),
    ("PKBK", 0, "2026-07-28.md", None),
    ("ZBRA", 0, "2026-07-28.md", None),
    ("MPWR", 0, "2026-07-28.md", None),
    ("VIR", 0, "2026-07-28.md", None),
    ("PMN", 0, "2026-07-28.md", None),
    ("GBLI", 0, "discovery_pool gen 2", None),
    ("GIND", 0, "discovery_pool gen 2", None),
    ("GMEX", 0, "discovery_pool gen 2", None),
    ("IBTH", 0, "discovery_pool gen 2", None),
    ("TMED", 1, None, "$34.26, RSI 55.9, MACDh +0.6131, vol 3.66x but 25k shares thin. Monitor -- absolute liquidity suspect."),
    ("BSVO", 1, None, "$29.32, RSI 56.5, MACDh +0.2847, vol 0.56x. Low volume -- disqualified."),
    ("YSXT", 1, None, "$0.86, RSI 42.3 below band, MACD bearish, vol 0.03x. Penny stock -- disqualified."),
    ("TKLF", 1, None, "$2.06, RSI 45.9 borderline, near-zero MACDh, vol 0.02x. Microcap -- disqualified."),
    ("NTRS", 21, None, "$180.52, RSI 53.6, vol 1.19x. MS UW PT $173 below current price -- disqualified. Financial."),
    ("GL", 21, None, "$173.48, RSI 45.1, vol 1.18x. KBW Outperform PT $190. Disqualified: bankroll ceiling. Insurance."),
    ("DXC", 21, None, "$10.05, RSI 59.2, MACDh near-zero, vol 0.77x <1.0x. Technology."),
    ("RMD", 21, None, "$195.27, RSI 45.7, MACD bearish, vol 0.66x. Healthcare."),
    ("AVEX", 21, None, "$14.29, RSI 39.3 below band, MACD bearish, vol 0.36x. Defense/aerospace."),
    ("PL", 21, None, "$20.47, RSI 27.9 oversold, MACD bearish, vol 1.21x, sentiment -0.445. Earth observation satellites."),
    ("TOST", 21, None, "$29.04, RSI 53.5, MACD +0.9865, vol 0.38x too thin. Truist PT $33. Sentiment +0.768. Restaurant tech."),
    ("BKSY", 21, None, "$21.57, RSI 35.8 below band, MACD bearish, vol 0.86x, -5.31% that day. Defense imaging."),
    ("RCAT", 21, None, "$7.64, RSI 37.4 below band, MACD bearish, vol 0.69x, -4.86% that day, sentiment +0.212. Defense/drones."),
]

# Hand-transcribed 2026-07-28 from watchlist.md's "## Closed Positions"
# section (deduped -- ITRI appeared twice, once there and once as a
# struck-through line under "Currently Held"). realized_pnl in absolute
# dollars isn't reliably derivable for most of these from the historical
# record (only a % was logged, not always the exact share count/entry
# price) -- left None rather than fabricated, per "leave None, don't
# guess." realized_return_pct is populated wherever the record gave one.
CLOSED_POSITIONS = [
    ("KRC", "2026-07-28T09:41:00-04:00", "trailing stop breach, 2sh via parallel collision", -5.7),
    ("FRNM", "2026-07-28T09:36:00-04:00", "trailing stop breach, parallel collision 2sh", -6.4),
    ("ITRI", "2026-07-28T12:41:00-04:00", "trailing stop breach", -4.38),
    ("BCS", "2026-07-28T09:30:00-04:00", "trailing stop breach", 1.1),
    ("FHB", "2026-07-27T09:59:00-04:00", "MACDh flip, v1.9 mandatory exit", -2.41),
    ("IP", "2026-07-27T09:58:00-04:00", "unintentional parallel-process sale", 10.62),
    ("F", "2026-07-27T09:37:00-04:00", "pre-earnings exit before Q2 7/28", 4.07),
    ("WSC", "2026-07-23T00:00:00-04:00", "MACDh flip, v1.4 mandatory exit", 1.46),
    ("NVDA", "2026-07-23T00:00:00-04:00", "MACDh flip, v1.4 mandatory exit", 0.45),
    ("CHWY", "2026-07-23T00:00:00-04:00", "MACDh flip, v1.4 mandatory exit", -0.02),
    ("KHC", "2026-07-23T00:00:00-04:00", "MACDh flip, v1.4 mandatory exit", -1.16),
    ("DVN", "2026-07-23T00:00:00-04:00", "MACDh flip, v1.4 mandatory exit", 1.89),
    ("SNAP", "2026-07-23T00:00:00-04:00", "MACDh flip, v1.4 mandatory exit", -4.59),
    ("GME", "2026-07-23T00:00:00-04:00", "trailing stop breach", -5.00),
    ("OPEN", "2026-07-23T00:00:00-04:00", "trailing stop breach", -5.11),
    ("SOFI", "2026-07-22T00:00:00-04:00", "broke $17 support, Game Plan cut", -4.58),
    ("LYFT", "2026-07-22T00:00:00-04:00", "trailing stop breach", -5.49),
    ("DJT", "2026-07-22T00:00:00-04:00", "trailing stop breach", -5.2),
    ("MVST", "2026-07-22T00:00:00-04:00", "trailing stop breach", 0.29),
    ("FUBO", "2026-07-21T00:00:00-04:00", "MACDh bearish flip", -5.04),
    ("AMC", "2026-07-21T00:00:00-04:00", "trailing stop breach", -1.69),
    ("MARA", "2026-07-21T00:00:00-04:00", "trailing stop breach", 2.11),
]


def read_open_positions() -> list[dict]:
    """Live read of positions/*.md for ticker/shares/entry_price, merged
    with the hand-transcribed context above for entry_time/sector/thesis."""
    rows = []
    for f in sorted(POSITIONS_DIR.glob("*.md")):
        ticker = f.stem.upper()
        m = ENTRY_RE.search(f.read_text())
        if not m:
            print(f"WARNING: could not parse Entry line from {f.name}, skipping", file=sys.stderr)
            continue
        entry_price, shares = float(m.group(1)), float(m.group(2))
        ctx = POSITION_CONTEXT.get(ticker, {"entry_time": None, "sector": None, "thesis": None})
        if ctx["entry_time"] is None:
            print(f"WARNING: no hand-transcribed context for {ticker}, entry_time will be missing", file=sys.stderr)
        rows.append({
            "ticker": ticker, "shares": shares, "entry_price": entry_price,
            "entry_time": ctx["entry_time"] or "unknown", "sector": ctx["sector"], "thesis": ctx["thesis"],
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    open_positions = read_open_positions()
    open_tickers = {p["ticker"] for p in open_positions}
    candidates = [c for c in CANDIDATES if c[0] not in open_tickers]
    skipped_candidates = [c[0] for c in CANDIDATES if c[0] in open_tickers]

    summary = {
        "open_positions": open_positions,
        "watchlist_candidates_to_seed": len(candidates),
        "watchlist_candidates_skipped_already_a_position": skipped_candidates,
        "closed_positions_to_seed": len(CLOSED_POSITIONS),
    }
    print(json.dumps(summary, indent=2))

    if args.dry_run:
        print("\nDRY RUN -- nothing written.")
        return 0

    conn = trader_db.get_conn()
    try:
        for p in open_positions:
            trader_db.upsert_position(
                conn, ticker=p["ticker"], shares=p["shares"], entry_price=p["entry_price"],
                entry_time=p["entry_time"], sector=p["sector"], thesis=p["thesis"],
            )
        for ticker, idle_ticks, source, note in candidates:
            trader_db.upsert_watchlist_candidate(conn, ticker=ticker, source=source, note=note)
            if idle_ticks:
                conn.execute("UPDATE watchlist_candidates SET idle_ticks = ? WHERE ticker = ?", (idle_ticks, ticker))
                conn.commit()
        for ticker, closed_at, reason, return_pct in CLOSED_POSITIONS:
            trader_db.upsert_position(conn, ticker=ticker, shares=0, entry_price=0.0, entry_time=closed_at)
            trader_db.close_position(
                conn, ticker=ticker, closed_at=closed_at, close_reason=reason,
                realized_pnl=None, realized_return_pct=return_pct,
            )
    finally:
        conn.close()

    print("\nSeeded real state/trader.db. Verify via trader_query.py before deleting positions/*.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
