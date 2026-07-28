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
    python3 scripts/discovery_urgency_check.py [--cash-threshold-pct 70] [--min-candidates 3] [--empty-floor 0]

Thresholds default to params.json's watchlist.discovery_urgency block if unset
here (falling back to the module constants below if that's also missing).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discovery_scan  # noqa: E402
import discovery_daemon  # noqa: E402
import executor  # noqa: E402
import trader_db  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PARAMS_PATH = REPO_ROOT / "params.json"

DEFAULT_CASH_THRESHOLD_PCT = 70.0
DEFAULT_MIN_CANDIDATES = 3
DEFAULT_EMPTY_FLOOR = 0  # Raf, 2026-07-27: "if we don't have any symbols in our
# watchlist then we should immediately be looking for new ones" — hard trigger
# regardless of cash deployment, independent of cash_threshold_pct/min_candidates.


def _params_defaults() -> dict:
    """watchlist.discovery_urgency block in params.json, falling back to the
    module constants above if the file/key is missing (same defensive pattern
    discovery_scan.get_universe_price_band() already uses for params.json reads)."""
    try:
        params = json.loads(PARAMS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        params = {}
    du = params.get("watchlist", {}).get("discovery_urgency", {})
    return {
        "cash_threshold_pct": du.get("cash_threshold_pct", DEFAULT_CASH_THRESHOLD_PCT),
        "min_candidates": du.get("min_candidates", DEFAULT_MIN_CANDIDATES),
        "empty_floor": du.get("empty_floor", DEFAULT_EMPTY_FLOOR),
    }


def count_watchlist_candidates(conn) -> int:
    """Depth of the candidate pipeline, scoped to just candidates (not held
    positions, which don't indicate whether discovery itself is starved).
    Migrated 2026-07-28 from struck-through-line text parsing -- dropped
    rows are actually deleted from watchlist_candidates now, so a plain
    count needs no skip logic at all."""
    return len(trader_db.get_watchlist_candidates(conn))


def check_and_maybe_discover(cash_threshold_pct: float = None,
                              min_candidates: int = None,
                              empty_floor: int = None,
                              account: str = "stonks") -> dict:
    defaults = _params_defaults()
    cash_threshold_pct = defaults["cash_threshold_pct"] if cash_threshold_pct is None else cash_threshold_pct
    min_candidates = defaults["min_candidates"] if min_candidates is None else min_candidates
    empty_floor = defaults["empty_floor"] if empty_floor is None else empty_floor

    account_data = executor.get_account(account)
    equity = float(account_data.get("equity", 0))
    cash = float(account_data.get("cash", 0))
    cash_pct = (cash / equity * 100) if equity > 0 else 0.0

    conn = trader_db.get_conn()
    try:
        candidate_count = count_watchlist_candidates(conn)
    finally:
        conn.close()

    under_deployed = cash_pct >= cash_threshold_pct
    thin_pipeline = candidate_count < min_candidates
    pipeline_empty = candidate_count <= empty_floor

    health = discovery_daemon.daemon_health()

    result = {
        "cash_pct": round(cash_pct, 2),
        "candidate_count": candidate_count,
        "under_deployed": under_deployed,
        "thin_pipeline": thin_pipeline,
        "pipeline_empty": pipeline_empty,
        "triggered": (under_deployed and thin_pipeline) or pipeline_empty,
        # 2026-07-27: informational only -- does NOT change triggered logic
        # above. The continuous discovery_daemon.py (systemd, not a cron)
        # is the primary source of fresh watchlist candidates now; this
        # trigger stays armed exactly as before as defense-in-depth if the
        # daemon ever dies, now with an honest breadcrumb for why it fired.
        "daemon_healthy": health["healthy"],
        "daemon_last_cycle_completed_at": health["last_cycle_completed_at"],
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
    parser.add_argument("--cash-threshold-pct", type=float, default=None)
    parser.add_argument("--min-candidates", type=int, default=None)
    parser.add_argument("--empty-floor", type=int, default=None)
    args = parser.parse_args()

    result = check_and_maybe_discover(
        cash_threshold_pct=args.cash_threshold_pct,
        min_candidates=args.min_candidates,
        empty_floor=args.empty_floor)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
