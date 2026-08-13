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
from typing import Optional

import yfinance

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discovery_db  # noqa: E402
import discovery_scan  # noqa: E402
import discovery_screen  # noqa: E402
import executor  # noqa: E402 — reuse _is_regular_trading_hours() for dual-rate interval selection
import trader_db  # noqa: E402 — downstream-flow half of daemon_health(), see its docstring
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
    "daemon_health_stale_multiplier": 10,
    "downstream_flow_stale_seconds": 1800,
    "fundamentals_enrich_interval_seconds": 900,
    "fundamentals_enrich_max_per_cycle": 15,
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
    # 2026-08-10: last_finbert_confirmation_at updates on every attempt
    # regardless of outcome (it's a cadence gate, not a health signal) --
    # confirmed live, it looked freshly "healthy" the whole week the real
    # FinBERT worker was unreachable ~90-98% of the time and every score
    # was silently the keyword-scorer fallback instead. These track the
    # real outcome, from news_collector.LAST_WORKER_CALL_OK.
    "last_finbert_success_at": None,
    "last_finbert_failure_at": None,
    "cycles_completed_lifetime": 0,
    "last_fundamentals_enrich_at": None,
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
    candidates. Any exception from that leg (Alpaca News + FinBERT via the
    gpu-compute worker, SENTIMENT_JOB_TIMEOUT-bounded) is caught here and
    never propagates -- this is the one leg with a real, previously-
    undocumented network cost, and it must not be able to block or kill the
    cheap screening loop."""
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
        # score_sentiment()/score_sentiment_batch() fall back to the keyword
        # scorer silently -- no exception reaches here even when the worker
        # never responded, which is exactly how this went unnoticed for a
        # week. LAST_WORKER_CALL_OK reflects the real outcome of the last
        # worker round-trip made inside confirm_with_news() above.
        worker_ok = discovery_scan.news_collector.LAST_WORKER_CALL_OK
        if worker_ok is True:
            cursor_state["last_finbert_success_at"] = now
        elif worker_ok is False:
            cursor_state["last_finbert_failure_at"] = now
    except Exception:
        cursor_state["last_finbert_failure_at"] = now  # best-effort -- never block/kill the cheap loop over a flaky news/FinBERT call

    cursor_state["last_finbert_confirmation_at"] = now
    return cursor_state


def _fetch_fundamentals(ticker: str) -> dict:
    """Thin wrapper over yfinance.Ticker(ticker).info -- free, no key,
    deliberately standalone (not routed through data_bus.py, same "zero
    dependency" reliability principle as the rest of this daemon, see
    module docstring). Isolated in its own function so tests can
    monkeypatch just the network boundary, matching
    discovery_scan.confirm_with_news()'s role for the FinBERT leg. Raises
    on failure -- callers catch."""
    info = yfinance.Ticker(ticker).info
    return {"sector": info.get("sector"), "industry": info.get("industry"), "market_cap": info.get("marketCap")}


def enrich_candidate_fundamentals(conn, tickers: list, fetch_fn=_fetch_fundamentals) -> int:
    """Best-effort sector/industry/market_cap lookup for `tickers` (already
    filtered by the caller to in-band + sector IS NULL). Each ticker's
    fetch is independently try/excepted -- one bad symbol must never skip
    or block the rest of the batch. A ticker that comes back with nothing
    usable is left NULL, which makes it eligible for retry on a later
    cycle rather than permanently stuck. Returns the count actually
    written (for logging/tests)."""
    enriched = 0
    for ticker in tickers:
        try:
            data = fetch_fn(ticker)
        except Exception:
            continue
        sector, industry, market_cap = data.get("sector"), data.get("industry"), data.get("market_cap")
        if sector is None and industry is None and market_cap is None:
            continue
        discovery_db.record_fundamentals(conn, ticker, sector, industry, market_cap)
        enriched += 1
    return enriched


def maybe_enrich_fundamentals(conn, cursor_state: dict, config: dict, now: str = None) -> dict:
    """Separate, slower cadence than the screening loop -- same pattern as
    maybe_confirm_news() above. yfinance is an unauthenticated, occasionally
    slow/flaky free API; this must never block or kill the cheap screening
    loop, so the whole lookup batch is wrapped in one try/except in addition
    to enrich_candidate_fundamentals()'s own per-ticker handling.

    Only targets in-band candidates whose sector is still NULL (never
    successfully enriched) -- capped at fundamentals_enrich_max_per_cycle
    per run to bound latency, ordered by last_in_band_at DESC so the most
    currently-relevant candidates get priority over long-stale ones."""
    now = now or _now_iso()
    cursor_state = dict(cursor_state)
    last = cursor_state.get("last_fundamentals_enrich_at")
    if last is not None:
        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(last)).total_seconds()
        if elapsed < config["fundamentals_enrich_interval_seconds"]:
            return cursor_state

    try:
        rows = conn.execute(
            """SELECT ticker FROM candidates WHERE in_band = 1 AND sector IS NULL
               ORDER BY last_in_band_at DESC LIMIT ?""",
            (config["fundamentals_enrich_max_per_cycle"],),
        ).fetchall()
        tickers = [r["ticker"] for r in rows]
        if tickers:
            enrich_candidate_fundamentals(conn, tickers)
    except Exception:
        pass  # best-effort -- never block/kill the cheap screening loop

    cursor_state["last_fundamentals_enrich_at"] = now
    return cursor_state


def _sentiment_worker_ok(state: dict) -> Optional[bool]:
    """2026-08-10: separate from the loop's own `healthy` flag on purpose --
    a degraded sentiment worker doesn't mean the discovery loop itself is
    broken, but it was invisible either way since last_finbert_confirmation_at
    updates on every attempt regardless of outcome. True/False once either
    outcome has been recorded at least once; None if neither has (fresh
    daemon, or a version before this field existed)."""
    success_at = state.get("last_finbert_success_at")
    failure_at = state.get("last_finbert_failure_at")
    if success_at is None and failure_at is None:
        return None
    if failure_at is None:
        return True
    if success_at is None:
        return False
    return datetime.fromisoformat(success_at) >= datetime.fromisoformat(failure_at)


def _downstream_flow_check(now: str, config: dict, pool_db_path: Path = None,
                            trader_db_path: Path = None) -> dict | None:
    """2026-08-12: daemon_health() previously only checked that the
    screening cycle itself was alive -- it had no way to notice the pool
    filling up with real candidates while promote_candidates.py silently
    stopped landing any of them in watchlist_candidates (exactly the
    failure shape of the 2026-08-03 merge_discoveries.py regex bug, which
    silently rejected 68-81% of the watchlist for a week before anyone
    caught it by reading the journal). Only meaningful when there's
    actually something to promote and something scheduled to promote it --
    returns None (not applicable, folds into healthy=True) outside market
    hours or when the pool has nothing fresh in-band, rather than flagging
    a false positive for "nothing happened because there was nothing to
    do." """
    if not executor._is_regular_trading_hours():
        return None

    pool_conn = discovery_db.get_conn(pool_db_path)
    try:
        stats = discovery_db.pool_stats(pool_conn, now=now)
    finally:
        pool_conn.close()
    if stats["fresh_in_band_count"] == 0:
        return None

    conn = trader_db.get_conn(trader_db_path)
    try:
        last_touch = trader_db.most_recent_watchlist_source_touch(conn, "discovery_pool")
    finally:
        conn.close()

    if last_touch is None:
        elapsed = None
        ok = False
    else:
        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(last_touch)).total_seconds()
        ok = elapsed <= config["downstream_flow_stale_seconds"]

    return {
        "ok": ok,
        "fresh_in_band_count": stats["fresh_in_band_count"],
        "last_watchlist_touch": last_touch,
        "elapsed_seconds": elapsed,
    }


def daemon_health(max_staleness_seconds: int = None, now: str = None, cursor_state_path: Path = None,
                   pool_db_path: Path = None, trader_db_path: Path = None) -> dict:
    """Reads state/discovery_daemon.json. max_staleness_seconds defaults to
    daemon_health_stale_multiplier x whichever interval is currently in
    effect (market/off-hours), so the slower off-hours cadence doesn't
    spuriously read as unhealthy.

    Also checks the downstream half of the pipeline (pool -> watchlist),
    not just whether this daemon's own screening cycle is alive -- see
    _downstream_flow_check()'s docstring."""
    now = now or _now_iso()
    state = load_cursor_state(cursor_state_path)
    last_completed = state.get("last_cycle_completed_at")
    config = load_config()

    if last_completed is None:
        return {"healthy": False, "last_cycle_completed_at": None, "last_cycle_status": state.get("last_cycle_status"),
                "sentiment_worker_ok": _sentiment_worker_ok(state), "downstream_flow": None}

    if max_staleness_seconds is None:
        interval = (config["market_hours_interval_seconds"] if executor._is_regular_trading_hours()
                    else config["off_hours_interval_seconds"])
        max_staleness_seconds = interval * config["daemon_health_stale_multiplier"]

    elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(last_completed)).total_seconds()
    cycle_healthy = elapsed <= max_staleness_seconds

    downstream = _downstream_flow_check(now, config, pool_db_path=pool_db_path, trader_db_path=trader_db_path)
    downstream_healthy = downstream is None or downstream["ok"]

    return {
        "healthy": cycle_healthy and downstream_healthy,
        "last_cycle_completed_at": last_completed,
        "last_cycle_status": state.get("last_cycle_status"),
        "sentiment_worker_ok": _sentiment_worker_ok(state),
        "downstream_flow": downstream,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run a single cycle then exit")
    parser.add_argument("--db-path", default=None, help="Override state/discovery_pool.db (dry-run/tests)")
    parser.add_argument("--check-health", action="store_true",
                         help="Print daemon_health() as JSON and exit 0 if healthy, 1 if not. "
                              "No daemon loop, no network -- just reads state/discovery_daemon.json. "
                              "Meant for a cron's trigger-script/command gate, e.g. "
                              "'python3 scripts/discovery_daemon.py --check-health' exits nonzero "
                              "only when the daemon is actually down/stale, so a health-check cron "
                              "can stay silent on every healthy run and only alert on failure.")
    args = parser.parse_args()

    if args.check_health:
        health = daemon_health()
        print(json.dumps(health, indent=2))
        sys.exit(0 if health["healthy"] else 1)

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
        cursor_state = maybe_enrich_fundamentals(conn, cursor_state, config, now=now)
        save_cursor_state(cursor_state)

        if args.once:
            break

        interval = (config["market_hours_interval_seconds"] if executor._is_regular_trading_hours()
                    else config["off_hours_interval_seconds"])
        time.sleep(interval)

    conn.close()


if __name__ == "__main__":
    main()
