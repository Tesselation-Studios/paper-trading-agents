#!/usr/bin/env python3
"""
backtest_session.py — create and manage isolated per-session SQLite files
for the historical-replay harness (2026-08-01).

Design (per the historical-training-harness plan): each replay chain gets
its OWN SQLite file, seeded from the most recent stonks-db-backup snapshot,
never the live state/trader.db directly. This sidesteps the positions
table's `ticker TEXT PRIMARY KEY` collision problem entirely (a backtest
AAPL row can never collide with a live AAPL row -- they're physically
different files) and means executor.py's gates need zero live/backtest
position-source threading, since a session's DB simply never contains any
live rows to accidentally double-count against.

A session also gets a small sibling manifest file (state/backtest/<id>.manifest.json)
tracking chain state (current_date, status, etc.) across separate cron
fires with no in-memory state -- matching this codebase's own "files, not
session context, are the persistence layer" principle (v4-spec.md).

Usage:
    python3 scripts/backtest_session.py create <session-id> --start-date 2026-06-01 [--starting-capital 10000]
    python3 scripts/backtest_session.py get <session-id>
    python3 scripts/backtest_session.py advance <session-id> --date 2026-06-02
    python3 scripts/backtest_session.py complete <session-id>
"""
import argparse
import datetime
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
BACKUPS_DIR = WORKSPACE_DIR / "state" / "backups"
BACKTEST_DIR = WORKSPACE_DIR / "state" / "backtest"

STARTING_CASH_DEFAULT = 10_000.00
# Mirrors bankroll.py's live defaults -- gate_bankroll is disabled for
# backtest sessions entirely (see executor.py's run_gates() toggles), so
# these values are never actually consulted for gating; they exist purely
# so bankroll_state has a sane, non-stale row rather than carrying over
# whatever the live ceiling happened to be at backup time.
BANKROLL_GROWTH_RATE_DEFAULT = 0.02
BANKROLL_DECAY_RATE_DEFAULT = 0.01
BANKROLL_TARGET_PROFIT_PCT_DEFAULT = 0.01

# Tables that hold live, mutable trading state -- cleared on session
# creation so a replay doesn't inherit today's real positions/history.
# news_cache is deliberately NOT in this list: it's reference data (real
# timestamped articles), useful context for a replayed day, not something
# that needs resetting.
LIVE_STATE_TABLES = [
    "positions",
    "decisions",
    "journal",
    "training_examples",
    "watchlist_candidates",
    "alpaca_audit_log",
    "bankroll_history",
]


def _latest_backup() -> Path | None:
    backups = sorted(BACKUPS_DIR.glob("trader-*.db"))
    return backups[-1] if backups else None


def _manifest_path(session_id: str) -> Path:
    return BACKTEST_DIR / f"{session_id}.manifest.json"


def _db_path(session_id: str) -> Path:
    return BACKTEST_DIR / f"{session_id}.db"


def resolve_session_db_path(session_id: str) -> Path:
    """Used by replay_order.py/prepare_tick_replay.py to find a session's
    DB file. Fails loudly (not a fallback to state/trader.db) if the
    session doesn't exist -- a backtest script should never silently end
    up pointed at live data."""
    manifest = get_session(session_id)
    if manifest is None:
        raise FileNotFoundError(f"no backtest session found for id={session_id!r}")
    db_path = Path(manifest["db_path"])
    if not db_path.exists():
        raise FileNotFoundError(f"session {session_id!r}'s DB file is missing: {db_path}")
    return db_path


def get_session(session_id: str) -> dict | None:
    path = _manifest_path(session_id)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write_manifest(manifest: dict) -> None:
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    _manifest_path(manifest["id"]).write_text(json.dumps(manifest, indent=2))


def create_session(session_id: str, start_date: str, end_date: str = None,
                    starting_capital: float = STARTING_CASH_DEFAULT) -> dict:
    """Creates state/backtest/<session_id>.db seeded from the latest
    stonks-db-backup snapshot (falls back to a fresh empty schema if no
    backup exists yet -- e.g. a brand-new workspace, before the first
    backup cron has run), clears live-only mutable state, resets
    bankroll_state to starting_capital, and writes the session manifest.

    Idempotent guard: refuses to overwrite an existing session with the
    same id -- use get_session()/advance_session() for an in-progress one."""
    if get_session(session_id) is not None:
        raise ValueError(f"session {session_id!r} already exists -- use start_day()/complete_day() instead")

    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _db_path(session_id)

    backup = _latest_backup()
    if backup is not None:
        shutil.copy2(backup, db_path)
        seeded_from = str(backup)
    else:
        # No backup exists yet -- start from a genuinely fresh, empty schema
        # rather than failing outright. Logged in the manifest so it's
        # visible this session has no news_cache/reference data carried over.
        seeded_from = None

    # init_schema() is idempotent (CREATE TABLE IF NOT EXISTS + additive
    # _migrate_add_column checks) -- safe whether db_path is a fresh file
    # or a full copy of an already-migrated live DB.
    conn = trader_db.get_conn(db_path)
    try:
        with conn:
            for table in LIVE_STATE_TABLES:
                conn.execute(f"DELETE FROM {table}")
            trader_db.upsert_bankroll_state(
                conn,
                ceiling=starting_capital,
                growth_rate=BANKROLL_GROWTH_RATE_DEFAULT,
                decay_rate=BANKROLL_DECAY_RATE_DEFAULT,
                target_profit_pct=BANKROLL_TARGET_PROFIT_PCT_DEFAULT,
                ceiling_pct=1.0,  # backtest bankroll isn't equity-scaled -- gate_bankroll is disabled anyway
            )
    finally:
        conn.close()

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    manifest = {
        "id": session_id,
        "db_path": str(db_path),
        "seeded_from_backup": seeded_from,
        "started_at": now,
        "ended_at": None,
        "start_date": start_date,
        "end_date": end_date,
        "current_date": None,       # last FULLY COMPLETED replay day; None until the first day finishes
        "day_in_progress": None,    # set by prepare step, cleared on successful completion -- see advance_session()
        "starting_capital": starting_capital,
        "status": "active",
        "notes": "",
    }
    _write_manifest(manifest)
    return manifest


def start_day(session_id: str, date_str: str) -> dict:
    """Two-phase completion, phase 1: reserve a day as in-progress BEFORE
    the agentTurn runs. If the agentTurn never completes (timeout, error),
    day_in_progress stays set and the day is retried, not silently marked
    done -- current_date only advances in complete_day()."""
    manifest = get_session(session_id)
    if manifest is None:
        raise FileNotFoundError(f"no backtest session found for id={session_id!r}")
    if manifest["status"] != "active":
        raise ValueError(f"session {session_id!r} is not active (status={manifest['status']!r})")
    manifest["day_in_progress"] = date_str
    _write_manifest(manifest)
    return manifest


def complete_day(session_id: str, date_str: str) -> dict:
    """Two-phase completion, phase 2: only called after the agentTurn's
    last action for the day (journal write) succeeds. Advances
    current_date and clears day_in_progress. Raises if date_str doesn't
    match the reserved day_in_progress -- a stale/out-of-order completion
    call is a bug, not something to silently accept."""
    manifest = get_session(session_id)
    if manifest is None:
        raise FileNotFoundError(f"no backtest session found for id={session_id!r}")
    if manifest.get("day_in_progress") != date_str:
        raise ValueError(
            f"session {session_id!r}: complete_day({date_str!r}) doesn't match "
            f"the reserved day_in_progress ({manifest.get('day_in_progress')!r})"
        )
    manifest["current_date"] = date_str
    manifest["day_in_progress"] = None
    if manifest.get("end_date") and date_str >= manifest["end_date"]:
        manifest["status"] = "completed"
        manifest["ended_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _write_manifest(manifest)
    return manifest


def compute_context(session_id: str, current_prices: dict = None) -> dict:
    """Builds the {"portfolio_value", "cash", "positions"} context shape
    executor.run_gates() expects, from a backtest session's own DB -- there's
    no live Alpaca account to query, so this is replay_order.py's substitute
    for check_order()'s get_account()/get_positions() calls.

    cash = starting_capital - cost basis of currently open positions +
    realized P&L of positions closed so far this session. current_prices
    (optional {ticker: price} map) marks each open position at its current
    price when the caller has one (e.g. the ticker it's evaluating this
    tick); any other open position falls back to its own entry_price as an
    approximate mark -- acceptable at the 15-min-snapshot granularity this
    harness operates at, not meant to be exact mark-to-market."""
    current_prices = current_prices or {}
    manifest = get_session(session_id)
    if manifest is None:
        raise FileNotFoundError(f"no backtest session found for id={session_id!r}")

    conn = trader_db.get_conn(Path(manifest["db_path"]))
    try:
        open_positions = trader_db.get_open_positions(conn)
        closed_positions = [
            p for p in conn.execute("SELECT * FROM positions WHERE status = 'closed'")
        ]
        realized_pnl = sum(p["realized_pnl"] or 0.0 for p in closed_positions)
    finally:
        conn.close()

    cost_basis = sum(p["shares"] * p["entry_price"] for p in open_positions)
    cash = manifest["starting_capital"] - cost_basis + realized_pnl

    positions_ctx = []
    portfolio_value = cash
    for p in open_positions:
        mark = current_prices.get(p["ticker"].upper(), p["entry_price"])
        market_value = p["shares"] * mark
        positions_ctx.append({"symbol": p["ticker"], "market_value": market_value})
        portfolio_value += market_value

    return {"portfolio_value": portfolio_value, "cash": cash, "positions": positions_ctx}


def abandon_session(session_id: str, reason: str = "") -> dict:
    manifest = get_session(session_id)
    if manifest is None:
        raise FileNotFoundError(f"no backtest session found for id={session_id!r}")
    manifest["status"] = "abandoned"
    manifest["ended_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if reason:
        manifest["notes"] = (manifest.get("notes") or "") + f"\nabandoned: {reason}"
    _write_manifest(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create")
    p_create.add_argument("session_id")
    p_create.add_argument("--start-date", required=True)
    p_create.add_argument("--end-date", default=None)
    p_create.add_argument("--starting-capital", type=float, default=STARTING_CASH_DEFAULT)

    p_get = sub.add_parser("get")
    p_get.add_argument("session_id")

    p_start_day = sub.add_parser("start-day")
    p_start_day.add_argument("session_id")
    p_start_day.add_argument("--date", required=True)

    p_complete_day = sub.add_parser("complete-day")
    p_complete_day.add_argument("session_id")
    p_complete_day.add_argument("--date", required=True)

    p_abandon = sub.add_parser("abandon")
    p_abandon.add_argument("session_id")
    p_abandon.add_argument("--reason", default="")

    args = parser.parse_args()

    try:
        if args.cmd == "create":
            result = create_session(args.session_id, args.start_date, args.end_date, args.starting_capital)
        elif args.cmd == "get":
            result = get_session(args.session_id)
            if result is None:
                print(json.dumps({"error": f"no session {args.session_id!r}"}))
                return 1
        elif args.cmd == "start-day":
            result = start_day(args.session_id, args.date)
        elif args.cmd == "complete-day":
            result = complete_day(args.session_id, args.date)
        elif args.cmd == "abandon":
            result = abandon_session(args.session_id, args.reason)
        else:
            return 1
    except (ValueError, FileNotFoundError) as e:
        print(json.dumps({"error": str(e)}))
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
