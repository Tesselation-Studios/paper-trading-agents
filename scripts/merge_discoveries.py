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

TICKER_HEADER_RE = re.compile(r"^## ([A-Z]{1,5}) — \$([0-9.]+)", re.MULTILINE)
RSI_LINE_RE = re.compile(r"^- RSI\(14\): ([0-9.]+)(?:, volume ([0-9.]+)x 20d avg)?", re.MULTILINE)
NEWS_LINE_RE = re.compile(r'^- News: "(.+?)" \(sentiment ([+-]?[0-9.]+|n/a)\)', re.MULTILINE)

# Signal columns carried straight through from a discovery-pool candidate
# dict onto the watchlist row. Keys are identical on both sides (the pool's
# candidates table and watchlist_candidates use the same column names), so
# this is a plain passthrough, not a mapping.
#
# sector/industry/market_cap (2026-08-13): discovery_daemon.py's yfinance
# enrichment writes these onto the pool row (see discovery_db.record_
# fundamentals()); carrying them through here is what lets executor.py
# auto-populate positions.sector at BUY time without an explicit --sector
# flag. Same passthrough contract -- absent/None on the source dict just
# means "not enriched yet", handled the same as any other missing signal.
SIGNAL_FIELDS = ("price", "rsi", "volume_ratio", "macd_hist", "sentiment", "news_headline",
                  "sector", "industry", "market_cap", "ma_filing_flag")


def latest_discoveries_file(date: str = None) -> Path | None:
    if date:
        p = DISCOVERIES_DIR / f"{date}.md"
        return p if p.exists() else None
    files = sorted(DISCOVERIES_DIR.glob("*.md"))
    return files[-1] if files else None


def extract_candidates(text: str) -> list[dict]:
    """Parse ## TICKER — $PRICE headers (plus the RSI/volume/sentiment lines
    written right under each one by discovery_scan.write_discoveries_file)
    out of a discoveries/*.md file.

    Was ticker-only until 2026-08-10 -- the header regex had no capture
    group for the price sitting right next to the ticker, so every
    discoveries/*.md candidate landed in watchlist_candidates with
    price=NULL and got silently rejected downstream (68-81% of the
    watchlist, confirmed live against real Alpaca quotes -- not a
    data-quotability gap, the price was right there in the file and just
    never parsed out). Same day, same fix needed for the sibling fields:
    RSI/volume_ratio/sentiment/news_headline were sitting right below the
    header too and were being thrown away the same way -- 45% of the
    watchlist entering decisions with real, already-computed technicals
    silently replaced with nothing.

    macd_hist is NOT parsed here because write_discoveries_file() never
    writes it in the first place for this candidate source -- nothing is
    being dropped at this layer, it was never captured upstream. Only
    discovery_pool.db-sourced candidates (the other ingestion path, see
    _split_candidate's dict branch) carry macd_hist."""
    candidates = []
    headers = list(TICKER_HEADER_RE.finditer(text))
    for i, m in enumerate(headers):
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[m.end():block_end]
        candidate = {"ticker": m.group(1), "price": float(m.group(2))}
        rsi_m = RSI_LINE_RE.search(block)
        if rsi_m:
            candidate["rsi"] = float(rsi_m.group(1))
            if rsi_m.group(2):
                candidate["volume_ratio"] = float(rsi_m.group(2))
        news_m = NEWS_LINE_RE.search(block)
        if news_m:
            candidate["news_headline"] = news_m.group(1)
            if news_m.group(2) != "n/a":
                candidate["sentiment"] = float(news_m.group(2))
        candidates.append(candidate)
    return candidates


def _split_candidate(entry) -> tuple[str, dict]:
    """Accept either a bare ticker string or a full candidate dict (from
    discoveries/*.md via extract_candidates(), or from the discovery pool),
    and return (TICKER, signals-to-write). Only non-None signals are
    returned, so a partially-populated row doesn't write NULLs over
    anything."""
    if isinstance(entry, str):
        return entry.upper(), {}
    return entry["ticker"].upper(), {
        field: entry[field] for field in SIGNAL_FIELDS if entry.get(field) is not None
    }


def insert_into_watchlist(candidates: list, source_label: str, dry_run: bool = False,
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
    fixes this structurally.

    2026-08-01: candidates may now be dicts, not just ticker strings, and
    their signals (price/rsi/volume_ratio/macd_hist/sentiment/
    news_headline) get written onto the row. Previously this wrote
    ticker+source only, so every signal discovery_daemon.py had already
    computed was thrown away at the pool->watchlist boundary and every
    tick re-derived it from scratch inside its time budget -- which is
    what degraded per-candidate analysis to a one-line MACD check. Bare
    strings still work unchanged for the discoveries/*.md path."""
    conn = trader_db.get_conn(db_path)
    try:
        max_size = json.loads(PARAMS_PATH.read_text()).get("watchlist", {}).get("max_size", 30)

        existing_tickers = {p["ticker"] for p in trader_db.get_all_positions(conn)}
        existing_tickers |= {c["ticker"] for c in trader_db.get_watchlist_candidates(conn)}
        active_candidate_count = len(trader_db.get_watchlist_candidates(conn))

        merged, skipped = [], []
        to_add = []
        for entry in candidates:
            ticker, signals = _split_candidate(entry)
            if ticker in existing_tickers:
                skipped.append(ticker)
                continue
            if active_candidate_count + len(to_add) >= max_size:
                skipped.append(f"{ticker} (max_size {max_size} reached)")
                continue
            to_add.append((ticker, signals))
            merged.append(ticker)

        if not dry_run:
            for ticker, signals in to_add:
                trader_db.upsert_watchlist_candidate(conn, ticker=ticker, source=source_label, **signals)
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
