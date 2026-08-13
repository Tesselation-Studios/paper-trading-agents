#!/usr/bin/env python3
"""
edgar_scan.py — free SEC EDGAR corporate-actions signal, added 2026-08-13.

Closes the BWMN data gap: Alpaca (this repo's sole market-data/news source,
see scripts/alpaca_client.py / scripts/news_collector.py) never carried
Bowman Consulting's 58%-premium acquisition announcement, and nothing in
this codebase watches for corporate actions/M&A at all (`grep -ri
"corporate action"` across the whole workspace was zero hits before this
file). This is a free, no-key supplement -- SEC's own data, not a paid
vendor -- deliberately best-effort/non-load-bearing, same posture as the
existing "data bus"/LoneStarOracle sources (see skills/data-bus-fallback.md):
informational context for Stan, never a gate.

Two SEC endpoints, both free and keyless (SEC requires only a descriptive
User-Agent identifying the requester, not an API key -- see
https://www.sec.gov/os/webmaster-faq#code-support):
  - https://www.sec.gov/files/company_tickers.json -- the full ticker->CIK
    map (~10k companies), refreshed weekly (state/edgar_ticker_cik_map.json).
  - https://data.sec.gov/submissions/CIK##########.json -- one company's
    recent filings (form type, filing date, item codes) -- no full-text
    search/document-parsing needed, since 8-K submissions already carry
    structured item codes (see MA_ITEM_CODES below).

Usage:
    python3 scripts/edgar_scan.py --tickers AAA,BBB
    python3 scripts/edgar_scan.py            # scans current watchlist + discovery pool
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discovery_db  # noqa: E402
import trader_db  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
TICKER_CIK_MAP_PATH = STATE_DIR / "edgar_ticker_cik_map.json"
TICKER_CIK_MAP_REFRESH_SECONDS = 7 * 86400  # weekly -- this list changes rarely (listings/delistings)

# SEC fair-access policy requires a real identifying contact, not a browser
# User-Agent -- an unidentified/browser-spoofing UA risks a 403/rate-limit.
USER_AGENT = "OpenClaw Stonks Trader research@wodinga.studio"
REQUEST_TIMEOUT_SECONDS = 10

# 8-K item codes that indicate an M&A-relevant event:
#   1.01 Entry into a Material Definitive Agreement (often a merger agreement)
#   2.01 Completion of Acquisition or Disposition of Assets
#   5.01 Changes in Control of Registrant
MA_ITEM_CODES = {"1.01", "2.01", "5.01"}


def _headers():
    return {"User-Agent": USER_AGENT}


def _load_ticker_cik_map(path: Path = TICKER_CIK_MAP_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_ticker_cik_map(data: dict, path: Path = TICKER_CIK_MAP_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2))
    tmp_path.replace(path)


def _fetch_ticker_cik_map_raw() -> dict:
    """Network boundary, isolated for test monkeypatching (matches
    discovery_daemon.py's _fetch_fundamentals() role). Raises on failure --
    callers catch. Returns {ticker: cik_str} built from SEC's raw
    {"0": {"cik_str": ..., "ticker": ..., "title": ...}, "1": {...}, ...}
    shape."""
    resp = requests.get("https://www.sec.gov/files/company_tickers.json",
                         headers=_headers(), timeout=REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    raw = resp.json()
    return {row["ticker"].upper(): str(row["cik_str"]) for row in raw.values()}


def get_ticker_cik_map(now: str = None, force_refresh: bool = False,
                        path: Path = TICKER_CIK_MAP_PATH,
                        fetch_fn=_fetch_ticker_cik_map_raw) -> dict:
    """Cached ticker->CIK map, refreshed weekly. Best-effort: a fetch
    failure on a stale/missing cache returns whatever's cached (possibly
    {}), never raises -- a scan with no map just finds nothing this run,
    which is the correct "supplemental signal, not load-bearing" behavior."""
    now = now or datetime.now(timezone.utc).isoformat()
    cached = _load_ticker_cik_map(path)
    fetched_at = cached.get("_fetched_at") if isinstance(cached, dict) else None
    stale = force_refresh or fetched_at is None
    if not stale:
        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(fetched_at)).total_seconds()
        stale = elapsed >= TICKER_CIK_MAP_REFRESH_SECONDS

    if not stale:
        return {k: v for k, v in cached.items() if k != "_fetched_at"}

    try:
        fresh = fetch_fn()
    except Exception:
        return {k: v for k, v in cached.items() if k != "_fetched_at"} if cached else {}

    to_save = dict(fresh)
    to_save["_fetched_at"] = now
    _save_ticker_cik_map(to_save, path)
    return fresh


def _fetch_submissions_raw(cik: str) -> dict:
    """Network boundary, isolated for test monkeypatching. Raises on
    failure -- callers catch."""
    padded = str(cik).zfill(10)
    resp = requests.get(f"https://data.sec.gov/submissions/CIK{padded}.json",
                         headers=_headers(), timeout=REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def _filing_url(cik: str, accession_number: str, primary_document: str) -> str:
    accession_no_dashes = accession_number.replace("-", "")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession_no_dashes}/{primary_document}")


def check_ticker_for_ma_filing(ticker: str, cik_map: dict, lookback_days: int = 7,
                                now: str = None, fetch_fn=_fetch_submissions_raw) -> list:
    """Returns a list of flagged 8-K filings (possibly empty) for `ticker`
    whose item codes intersect MA_ITEM_CODES and were filed within
    lookback_days of `now`. Any failure (unknown ticker, network error,
    malformed response) returns [] rather than raising -- best-effort,
    matches enrich_candidate_fundamentals()'s per-ticker isolation."""
    now = now or datetime.now(timezone.utc).isoformat()
    cik = cik_map.get(ticker.upper())
    if not cik:
        return []

    try:
        submissions = fetch_fn(cik)
        recent = submissions["filings"]["recent"]
        forms = recent["form"]
        dates = recent["filingDate"]
        items_list = recent.get("items", [""] * len(forms))
        accessions = recent["accessionNumber"]
        primary_docs = recent["primaryDocument"]
    except Exception:
        return []

    cutoff = datetime.fromisoformat(now) - timedelta(days=lookback_days)
    flagged = []
    for form, filing_date, items, accession, primary_doc in zip(forms, dates, items_list, accessions, primary_docs):
        if not form.startswith("8-K"):
            continue
        try:
            if datetime.fromisoformat(filing_date) < cutoff.replace(tzinfo=None):
                continue
        except ValueError:
            continue
        item_codes = {i.strip() for i in (items or "").split(",") if i.strip()}
        matched = item_codes & MA_ITEM_CODES
        if not matched:
            continue
        flagged.append({
            "ticker": ticker.upper(), "form": form, "filing_date": filing_date,
            "items": sorted(matched), "accession_number": accession,
            "filing_url": _filing_url(cik, accession, primary_doc),
        })
    return flagged


def scan_tickers(tickers: list, lookback_days: int = 7, now: str = None,
                  cik_map: dict = None, sleep_between_seconds: float = 0.15) -> dict:
    """Batch wrapper -- one ticker's failure never blocks the rest.
    check_ticker_for_ma_filing() already catches everything internally, but
    this loop wraps it too (defense-in-depth, matches this codebase's
    belt-and-suspenders posture elsewhere, e.g. discovery_daemon.py's
    maybe_confirm_news() around confirm_with_news()) so a future change to
    that function's error handling can't turn one bad ticker into a dead
    batch. A small sleep between requests is polite/conservative against
    SEC's published fair-access rate limits (10 req/sec) -- this scan is a
    slow, off-critical-path signal, not latency-sensitive. Returns only
    tickers with at least one flagged filing."""
    cik_map = cik_map if cik_map is not None else get_ticker_cik_map(now=now)
    results = {}
    for i, ticker in enumerate(tickers):
        if i > 0 and sleep_between_seconds:
            time.sleep(sleep_between_seconds)
        try:
            flagged = check_ticker_for_ma_filing(ticker, cik_map, lookback_days=lookback_days, now=now)
        except Exception:
            continue
        if flagged:
            results[ticker.upper()] = flagged
    return results


def _default_tickers() -> list:
    """No --tickers given: scan the current watchlist + in-band discovery
    pool, same universe the daemon's fundamentals enrichment targets."""
    tickers = set()
    conn = trader_db.get_conn()
    try:
        tickers |= {c["ticker"] for c in trader_db.get_watchlist_candidates(conn)}
    finally:
        conn.close()
    pool_conn = discovery_db.get_conn()
    try:
        rows = pool_conn.execute("SELECT ticker FROM candidates WHERE in_band = 1").fetchall()
        tickers |= {r["ticker"] for r in rows}
    finally:
        pool_conn.close()
    return sorted(tickers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", help="comma-separated, defaults to current watchlist + in-band discovery pool")
    parser.add_argument("--lookback-days", type=int, default=7)
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else _default_tickers()
    result = scan_tickers(tickers, lookback_days=args.lookback_days)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
