#!/usr/bin/env python3
"""
discovery_urgency_check.py — "always be doing discovery if he's been
mostly holding" (Raf, 2026-07-23).

Cheap, mechanical pre-check (one real Alpaca call + a local file read) —
if cash is most of the portfolio AND the watchlist candidate pipeline is
thin, runs scripts/discovery_scan.py immediately rather than waiting for
the next scheduled 2am/2pm probe-discovery slot. If deployment/pipeline
are healthy, this is a near-free no-op — safe to run frequently.

Usage:
    python3 scripts/discovery_urgency_check.py [--cash-threshold-pct 70] [--min-candidates 3]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discovery_scan  # noqa: E402
import executor  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_PATH = REPO_ROOT / "strategies" / "watchlist.md"

DEFAULT_CASH_THRESHOLD_PCT = 70.0
DEFAULT_MIN_CANDIDATES = 3


def count_watchlist_candidates(text: str) -> int:
    """Active (non-struck-through) entries under '## Candidates' only —
    same section-boundary parsing convention as
    replay_check.load_live_universe(), but scoped to just the candidate
    pipeline's depth, not held positions (which don't indicate whether
    discovery itself is starved)."""
    count = 0
    in_candidates = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Candidates"):
            in_candidates = True
            continue
        if in_candidates and stripped.startswith("##"):
            break
        if not in_candidates or not stripped.startswith("-"):
            continue
        if "~~" in stripped:
            continue
        if re.match(r"-\s*([A-Z]{1,5})\b", stripped):
            count += 1
    return count


def check_and_maybe_discover(cash_threshold_pct: float = DEFAULT_CASH_THRESHOLD_PCT,
                              min_candidates: int = DEFAULT_MIN_CANDIDATES,
                              account: str = "stonks") -> dict:
    account_data = executor.get_account(account)
    equity = float(account_data.get("equity", 0))
    cash = float(account_data.get("cash", 0))
    cash_pct = (cash / equity * 100) if equity > 0 else 0.0

    watchlist_text = WATCHLIST_PATH.read_text() if WATCHLIST_PATH.exists() else ""
    candidate_count = count_watchlist_candidates(watchlist_text)

    under_deployed = cash_pct >= cash_threshold_pct
    thin_pipeline = candidate_count < min_candidates

    result = {
        "cash_pct": round(cash_pct, 2),
        "candidate_count": candidate_count,
        "under_deployed": under_deployed,
        "thin_pipeline": thin_pipeline,
        "triggered": under_deployed and thin_pipeline,
    }

    if result["triggered"]:
        min_price, max_price = discovery_scan.get_universe_price_band()
        candidates = discovery_scan.screen_candidates()
        top = discovery_scan.confirm_with_news(candidates)
        path = discovery_scan.write_discoveries_file(top, min_price, max_price)
        result["discovery_run"] = {
            "candidates_screened": len(candidates),
            "top_written": [c["ticker"] for c in top],
            "discoveries_file": str(path),
        }

    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cash-threshold-pct", type=float, default=DEFAULT_CASH_THRESHOLD_PCT)
    parser.add_argument("--min-candidates", type=int, default=DEFAULT_MIN_CANDIDATES)
    args = parser.parse_args()

    result = check_and_maybe_discover(
        cash_threshold_pct=args.cash_threshold_pct, min_candidates=args.min_candidates)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
