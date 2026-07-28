#!/usr/bin/env python3
"""Merge unconsumed probe-discovery candidates into the local watchlist_candidates table.

Deterministic, no LLM call — fixes the gap where stonks-probe-discovery
generates good candidates into discoveries/YYYY-MM-DD.md but nothing
mechanically feeds them into the watchlist (previously relied on the LLM
remembering to do it manually each session; strategy.md v1.2.1 wrote a
prose rule about this on 2026-07-21 but the watchlist stayed empty).

Idempotent — safe to run every tick. Skips tickers already held/closed or
already a candidate, respects params.json's watchlist.max_size.

Usage:
    python3 scripts/merge_discoveries.py            # merge most recent discoveries file
    python3 scripts/merge_discoveries.py --date 2026-07-21
    python3 scripts/merge_discoveries.py --dry-run
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
DISCOVERIES_DIR = WORKSPACE_DIR / "discoveries"
PARAMS_PATH = WORKSPACE_DIR / "params.json"

TICKER_HEADER_RE = re.compile(r"^## ([A-Z]{1,5}) — \$", re.MULTILINE)


def latest_discoveries_file(date: str = None) -> Path | None:
    if date:
        p = DISCOVERIES_DIR / f"{date}.md"
        return p if p.exists() else None
    files = sorted(DISCOVERIES_DIR.glob("*.md"))
    return files[-1] if files else None


def extract_candidates(text: str) -> list[str]:
    return TICKER_HEADER_RE.findall(text)


def insert_into_watchlist(tickers: list[str], source_label: str, dry_run: bool = False,
                           db_path: Path = None) -> dict:
    """Dedup against every real ticker in the positions table (open AND
    closed -- a closed ticker shouldn't be re-added as a fresh candidate
    either) plus every existing watchlist candidate, respect params.json's
    watchlist.max_size, upsert via trader_db.upsert_watchlist_candidate().

    Migrated 2026-07-28 from a blunt text-splice into watchlist.md whose
    dedup (`re.findall(r"\\b([A-Z]{1,5})\\b", watchlist_text)`) matched any
    1-5 uppercase token anywhere in the file, including note text -- a
    note like "MS UW PT $173" would permanently phantom-block tickers
    MS/UW/PT from ever being added. Dedup against real ticker columns
    fixes this structurally. Signature unchanged (db_path is new and
    optional) so promote_candidates.py needs zero changes."""
    conn = trader_db.get_conn(db_path)
    try:
        max_size = json.loads(PARAMS_PATH.read_text()).get("watchlist", {}).get("max_size", 30)

        existing_tickers = {p["ticker"] for p in trader_db.get_all_positions(conn)}
        existing_tickers |= {c["ticker"] for c in trader_db.get_watchlist_candidates(conn)}
        active_candidate_count = len(trader_db.get_watchlist_candidates(conn))

        merged, skipped = [], []
        to_add = []
        for ticker in tickers:
            ticker = ticker.upper()
            if ticker in existing_tickers:
                skipped.append(ticker)
                continue
            if active_candidate_count + len(to_add) >= max_size:
                skipped.append(f"{ticker} (max_size {max_size} reached)")
                continue
            to_add.append(ticker)
            merged.append(ticker)

        if not dry_run:
            for ticker in to_add:
                trader_db.upsert_watchlist_candidate(conn, ticker=ticker, source=source_label)
    finally:
        conn.close()

    return {"merged": merged, "skipped": skipped}


def merge(dry_run: bool = False, date: str = None) -> dict:
    disc_file = latest_discoveries_file(date)
    if disc_file is None:
        return {"merged": [], "skipped": [], "error": "no discoveries file found"}

    candidates = extract_candidates(disc_file.read_text())
    if not candidates:
        return {"merged": [], "skipped": [], "error": f"no ticker headers found in {disc_file.name}"}

    result = insert_into_watchlist(candidates, source_label=disc_file.name, dry_run=dry_run)
    result["source"] = disc_file.name
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD, defaults to most recent discoveries file")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = merge(dry_run=args.dry_run, date=args.date)
    print(json.dumps(result, indent=2))
    sys.exit(1 if result.get("error") else 0)


if __name__ == "__main__":
    main()
