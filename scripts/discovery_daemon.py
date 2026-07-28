#!/usr/bin/env python3
"""
discovery_daemon.py — continuous background discovery scanner for Stan
(trader-stonks), added 2026-07-27.

Real long-running daemon (deploy/stonks-discovery-daemon.service), not
another OpenClaw cron -- this repo has zero daemon precedent otherwise
(everything else is cron -> short script -> exit), so this is a
deliberate, explicitly-decided exception, not a drift from convention.
Standalone: zero dependency on data_bus.py (that service has a documented
reliability history that's the exact reason discovery_scan.py exists as a
direct-Alpaca script in the first place).

Loops forever, rotating through chunks of the full tradable US-equity
universe (persisted cursor in state/discovery_daemon.json survives
crash+restart), screening each chunk via discovery_screen.screen_tickers()
and upserting results into discovery_db.py's SQLite pool
(state/discovery_pool.db) -- including screen failures, so
last_screened_at becomes a real staleness signal. News/FinBERT
confirmation (discovery_scan.confirm_with_news(), the one leg with real
undocumented network cost) runs on a much slower, separate cadence and
can never block or kill the cheap screening loop.

promote_candidates.py (separate script, called every tick from
tick_prompt.md) is the only consumer of the pool -- this daemon never
touches discoveries/*.md or watchlist.md directly.

Usage:
    python3 scripts/discovery_daemon.py              # run forever
    python3 scripts/discovery_daemon.py --once        # single cycle, exit
    python3 scripts/discovery_daemon.py --once --db-path state/scratch.db
"""
import argparse
import contextlib
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discovery_db  # noqa: E402
import discovery_scan  # noqa: E402
import discovery_screen  # noqa: E402
import executor  # noqa: E402 — reuse _is_regular_trading_hours() for dual-rate interval selection
import universe_scan  # noqa: E402

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = WORKSPACE_DIR / "state"
CURSOR_STATE_PATH = STATE_DIR / "discovery_daemon.json"
PARAMS_PATH = WORKSPACE_DIR / "params.json"

DEFAULTS = {
    "chunk_size": 75,
    "market_hours_interval_seconds": 120,
    "off_hours_interval_seconds": 900,
    "universe_refresh_interval_seconds": 86400,
    "finbert_confirm_interval_seconds": 1800,
    "finbert_confirm_top_n": 6,
    "daemon_health_stale_multiplier": 3,
}


def load_config():
    try:
        params = json.loads(PARAMS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        params = {}
    config = dict(DEFAULTS)
    config.update(params.get("discovery_daemon", {}))
    return config


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


@contextlib.contextmanager
def _locked(path: Path):
    """Advisory flock, mirrors set_watch.py's watches.json.lock pattern --
    only one process (this daemon) writes the cursor file, but readers
    (discovery_urgency_check.py's daemon_health() call) shouldn't ever see
    a torn write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / (path.name + ".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


DEFAULT_CURSOR_STATE = {
    "pid": None,
    "started_at": None,
    "cursor_position": 0,
    "universe_generation": None,
    "universe_size": 0,
    "universe_last_refreshed_at": None,
    "full_passes_completed": 0,
    "last_cycle_started_at": None,
    "last_cycle_completed_at": None,
    "last_cycle_status": None,
    "last_finbert_confirmation_at": None,
    "cycles_completed_lifetime": 0,
}


def load_cursor_state(path: Path = None) -> dict:
    path = path or CURSOR_STATE_PATH
    if not path.exists():
        return dict(DEFAULT_CURSOR_STATE)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CURSOR_STATE)
    state = dict(DEFAULT_CURSOR_STATE)
    state.update(data)
    return state


def save_cursor_state(state: dict, path: Path = None) -> None:
    path = path or CURSOR_STATE_PATH
    with _locked(path):
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(state, indent=2))
        os.replace(tmp_path, path)


def advance_cursor(position: int, chunk_size: int, universe_size: int):
    """Pure. Returns (next_position, wrapped) -- wrapped=True means this
    chunk's end reached/passed universe_size, caller should bump
    full_passes_completed. Handles universe_size==0 and chunk_size>=universe_size."""
    if universe_size <= 0:
        return 0, False
    next_position = position + chunk_size
    wrapped = next_position >= universe_size
    return next_position % universe_size, wrapped


def get_chunk(universe: list, position: int, chunk_size: int) -> list:
    """Pure. Always chunk_size tickers (or the whole universe if smaller),
    wraps around the end of the list within the same chunk if it overruns
    -- a chunk is never short near the wrap boundary."""
    n = len(universe)
    if n == 0:
        return []
    if chunk_size >= n:
        return list(universe)
    end = position + chunk_size
    if end <= n:
        return universe[position:end]
    return universe[position:n] + universe[0:end - n]


def refresh_universe_if_stale(conn, cursor_state: dict, refresh_interval_seconds: int,
                               now: str = None, force: bool = False):
    """Returns (universe: list[str], cursor_state: dict, refreshed: bool).
    Refetches the full tradable universe from Alpaca (universe_scan.fetch_broad_universe(
    sample_size=None)) when the last refresh is older than refresh_interval_seconds, or
    when force=True (e.g. no snapshot exists yet). Bumps universe_generation only on a
    real refresh."""
    now = now or _now_iso()
    last_refreshed = cursor_state.get("universe_last_refreshed_at")
    stale = force or last_refreshed is None
    if not stale:
        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(last_refreshed)).total_seconds()
        stale = elapsed >= refresh_interval_seconds

    if not stale:
        # cursor_state (state/discovery_daemon.json) is not tied to any one
        # db file -- verify the connection actually passed in agrees with
        # what the cursor claims before trusting it. Without this, a
        # --db-path swap (or a deleted/recreated db file) leaves the
        # cursor looking "fresh" while pointing at a db with an empty or
        # mismatched universe_snapshot table -- confirmed live 2026-07-27
        # during dry-run verification: a scratch --db-path run warmed the
        # cursor file, then the real db run silently got zero candidates
        # because it trusted the stale-but-fresh-looking timestamp instead
        # of checking its own snapshot table.
        db_generation = discovery_db.get_universe_generation(conn)
        db_universe = discovery_db.get_universe_snapshot(conn)
        if db_universe and db_generation == cursor_state.get("universe_generation"):
            return db_universe, cursor_state, False
        stale = True

    universe = universe_scan.fetch_broad_universe(sample_size=None)
    generation = (cursor_state.get("universe_generation") or 0) + 1
    discovery_db.upsert_universe_snapshot(conn, universe, generation, now)
    cursor_state = dict(cursor_state)
    cursor_state["universe_generation"] = generation
    cursor_state["universe_size"] = len(universe)
    cursor_state["universe_last_refreshed_at"] = now
    return universe, cursor_state, True


def run_cycle(conn, cursor_state: dict, config: dict, now: str = None) -> dict:
    """One screening cycle: refresh universe if stale, screen the next
    chunk, upsert results (winners and losers alike), advance the cursor,
    return the updated cursor_state. Does not persist to disk -- callers
    (main loop, tests) decide when to call save_cursor_state()."""
    now = now or _now_iso()
    cursor_state = dict(cursor_state)
    cursor_state["last_cycle_started_at"] = now

    universe, cursor_state, refreshed = refresh_universe_if_stale(
        conn, cursor_state, config["universe_refresh_interval_seconds"], now=now,
    )
    universe_size = len(universe)

    if universe_size == 0:
        cursor_state["last_cycle_status"] = "no_universe"
        cursor_state["last_cycle_completed_at"] = now
        return cursor_state

    # Guards a crash landing right after a refresh (or a universe that
    # shrank/grew since the last write) with a now-invalid stale position.
    cursor_state["cursor_position"] %= universe_size

    chunk = get_chunk(universe, cursor_state["cursor_position"], config["chunk_size"])
    min_price, max_price = discovery_scan.get_universe_price_band()
    screened = discovery_screen.screen_tickers(chunk, min_price, max_price, return_all=True)
    discovery_db.upsert_candidates(
        conn, screened, universe_generation=cursor_state["universe_generation"], screened_at=now,
    )

    next_position, wrapped = advance_cursor(cursor_state["cursor_position"], config["chunk_size"], universe_size)
    cursor_state["cursor_position"] = next_position
    if wrapped:
        cursor_state["full_passes_completed"] += 1

    cursor_state["last_cycle_status"] = "ok"
    cursor_state["last_cycle_completed_at"] = now
    cursor_state["cycles_completed_lifetime"] += 1
    return cursor_state


def maybe_confirm_news(conn, cursor_state: dict, config: dict, now: str = None) -> dict:
    """Separate, much slower cadence than the screening loop -- calls
    discovery_scan.confirm_with_news() on the current top in-band pool
    candidates. Any exception from that leg (Alpaca News + FinBERT POST to
    legend-of-macs.local, 8s timeout) is caught here and never propagates
    -- this is the one leg with a real, previously-undocumented network
    cost, and it must not be able to block or kill the cheap screening
    loop."""
    now = now or _now_iso()
    cursor_state = dict(cursor_state)
    last = cursor_state.get("last_finbert_confirmation_at")
    if last is not None:
        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(last)).total_seconds()
        if elapsed < config["finbert_confirm_interval_seconds"]:
            return cursor_state

    top_n = config["finbert_confirm_top_n"]
    top = discovery_db.get_top_candidates(conn, limit=top_n, max_age_seconds=config["universe_refresh_interval_seconds"], now=now)
    if not top:
        cursor_state["last_finbert_confirmation_at"] = now
        return cursor_state

    try:
        confirmed = discovery_scan.confirm_with_news(top, top_n=top_n)
        for c in confirmed:
            discovery_db.record_news_confirmation(
                conn, c["ticker"], c.get("sentiment"), c.get("news_headline"), now,
            )
    except Exception:
        pass  # best-effort -- never block/kill the cheap loop over a flaky news/FinBERT call

    cursor_state["last_finbert_confirmation_at"] = now
    return cursor_state


def daemon_health(max_staleness_seconds: int = None, now: str = None, cursor_state_path: Path = None) -> dict:
    """Reads state/discovery_daemon.json. max_staleness_seconds defaults to
    daemon_health_stale_multiplier x whichever interval is currently in
    effect (market/off-hours), so the slower off-hours cadence doesn't
    spuriously read as unhealthy."""
    now = now or _now_iso()
    state = load_cursor_state(cursor_state_path)
    last_completed = state.get("last_cycle_completed_at")

    if last_completed is None:
        return {"healthy": False, "last_cycle_completed_at": None, "last_cycle_status": state.get("last_cycle_status")}

    if max_staleness_seconds is None:
        config = load_config()
        interval = (config["market_hours_interval_seconds"] if executor._is_regular_trading_hours()
                    else config["off_hours_interval_seconds"])
        max_staleness_seconds = interval * config["daemon_health_stale_multiplier"]

    elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(last_completed)).total_seconds()
    healthy = elapsed <= max_staleness_seconds
    return {
        "healthy": healthy,
        "last_cycle_completed_at": last_completed,
        "last_cycle_status": state.get("last_cycle_status"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run a single cycle then exit")
    parser.add_argument("--db-path", default=None, help="Override state/discovery_pool.db (dry-run/tests)")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    config = load_config()
    conn = discovery_db.get_conn(db_path)
    cursor_state = load_cursor_state()
    cursor_state["pid"] = os.getpid()
    if cursor_state.get("started_at") is None:
        cursor_state["started_at"] = _now_iso()

    while True:
        now = _now_iso()
        cursor_state = run_cycle(conn, cursor_state, config, now=now)
        cursor_state = maybe_confirm_news(conn, cursor_state, config, now=now)
        save_cursor_state(cursor_state)

        if args.once:
            break

        interval = (config["market_hours_interval_seconds"] if executor._is_regular_trading_hours()
                    else config["off_hours_interval_seconds"])
        time.sleep(interval)

    conn.close()


if __name__ == "__main__":
    main()
