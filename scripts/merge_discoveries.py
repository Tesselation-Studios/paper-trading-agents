#!/usr/bin/env python3
"""Merge unconsumed probe-discovery candidates into strategies/watchlist.md.

Deterministic, no LLM call — fixes the gap where stonks-probe-discovery
generates good candidates into discoveries/YYYY-MM-DD.md but nothing
mechanically feeds them into the watchlist (previously relied on the LLM
remembering to do it manually each session; strategy.md v1.2.1 wrote a
prose rule about this on 2026-07-21 but the watchlist stayed empty).

Idempotent — safe to run every tick. Skips tickers already anywhere in
watchlist.md (held or listed), respects params.json's watchlist.max_size.

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

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
DISCOVERIES_DIR = WORKSPACE_DIR / "discoveries"
WATCHLIST_PATH = WORKSPACE_DIR / "strategies" / "watchlist.md"
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


def insert_into_watchlist(tickers: list[str], source_label: str, dry_run: bool = False) -> dict:
    """The '## Candidates' text-splice: dedup against every ticker anywhere
    in watchlist.md (held, listed, or dropped-note — deliberately
    conservative rather than trying to parse "dropped for cause"
    reasoning), respect params.json's watchlist.max_size, write
    '- TICKER — idle_ticks: 0 — from {source_label}' lines. Extracted
    2026-07-27 from merge()'s inline body so promote_candidates.py (the
    discovery-pool -> watchlist bridge) can reuse the exact same
    dedup/cap/format contract instead of reimplementing it."""
    watchlist_text = WATCHLIST_PATH.read_text()
    max_size = json.loads(PARAMS_PATH.read_text()).get("watchlist", {}).get("max_size", 30)

    existing_tickers = set(re.findall(r"\b([A-Z]{1,5})\b", watchlist_text))
    active_candidate_count = len(re.findall(r"^- [A-Z]{1,5} — idle_ticks:", watchlist_text, re.MULTILINE))

    merged, skipped = [], []
    new_lines = []
    for ticker in tickers:
        if ticker in existing_tickers:
            skipped.append(ticker)
            continue
        if active_candidate_count + len(new_lines) >= max_size:
            skipped.append(f"{ticker} (max_size {max_size} reached)")
            continue
        new_lines.append(f"- {ticker} — idle_ticks: 0 — from {source_label}")
        merged.append(ticker)

    if not new_lines:
        return {"merged": [], "skipped": skipped}

    if not dry_run:
        insertion = "\n".join(new_lines) + "\n\n"
        if "## Candidates\n" in watchlist_text:
            watchlist_text = watchlist_text.replace(
                "## Candidates\n", "## Candidates\n" + insertion, 1
            )
        else:
            watchlist_text += "\n## Candidates\n" + insertion
        WATCHLIST_PATH.write_text(watchlist_text)

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
