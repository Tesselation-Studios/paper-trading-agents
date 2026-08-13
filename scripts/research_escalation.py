#!/usr/bin/env python3
"""
research_escalation.py — selection query for the research-escalation
pipeline stage (2026-08-13).

Pure read/report, no side effects of its own -- the actual researcher
dispatch (a real sessions_send LLM-to-LLM conversation) has to happen
inside an agent turn, not a bare script, and the stonks-research-escalation
cron's agentTurn (see skills/research-escalation.md) is what calls this,
reads the JSON, has the conversation per candidate, then records the
outcome via `trader_write.py watchlist-record-research`.

Deliberately NOT run inside the live 290s stonks-tick budget: a single
sessions_send round-trip can run minutes (stonks-worldview-sync budgets
480s per wait), so this has its own separate cron cadence.

Usage:
    python3 scripts/research_escalation.py select
    python3 scripts/research_escalation.py select --cap 1 --cooldown-hours 12
    python3 scripts/research_escalation.py select --db-path state/scratch.db
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
PARAMS_PATH = WORKSPACE_DIR / "params.json"

DEFAULT_COOLDOWN_HOURS = 6
DEFAULT_CAP = 3


def _load_config() -> dict:
    try:
        params = json.loads(PARAMS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        params = {}
    watchlist = params.get("watchlist", {})
    escalation = watchlist.get("research_escalation", {})
    return {
        "interest_min_score": watchlist.get("interest_min_score", trader_db.DEFAULT_INTEREST_MIN_SCORE),
        "cooldown_hours": escalation.get("cooldown_hours", DEFAULT_COOLDOWN_HOURS),
        "cap": escalation.get("cap", DEFAULT_CAP),
        "max_rounds": escalation.get("max_rounds"),
    }


def select(db_path: Path = None, interest_min_score: int = None, cooldown_hours: float = None,
           cap: int = None, now: str = None) -> dict:
    config = _load_config()
    interest_min_score = interest_min_score if interest_min_score is not None else config["interest_min_score"]
    cooldown_hours = cooldown_hours if cooldown_hours is not None else config["cooldown_hours"]
    cap = cap if cap is not None else config["cap"]

    conn = trader_db.get_conn(db_path)
    try:
        candidates = trader_db.get_research_escalation_candidates(
            conn, interest_min_score=interest_min_score, cooldown_hours=cooldown_hours,
            cap=cap, now=now,
        )
    finally:
        conn.close()

    return {
        "interest_min_score": interest_min_score,
        "cooldown_hours": cooldown_hours,
        "cap": cap,
        "max_rounds": config["max_rounds"],
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", default=None, help="Override state/trader.db (dry-run/tests)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("select", help="List watchlist candidates due for research escalation")
    p.add_argument("--interest-min-score", type=int, default=None,
                    help="Default: params.json watchlist.interest_min_score")
    p.add_argument("--cooldown-hours", type=float, default=None,
                    help="Default: params.json watchlist.research_escalation.cooldown_hours")
    p.add_argument("--cap", type=int, default=None,
                    help="Default: params.json watchlist.research_escalation.cap")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None
    if args.command == "select":
        result = select(
            db_path=db_path, interest_min_score=args.interest_min_score,
            cooldown_hours=args.cooldown_hours, cap=args.cap,
        )
        print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
