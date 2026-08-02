#!/usr/bin/env python3
"""
Prepares one night's historical tick-replay practice session for Stan.

Picks the next not-yet-replayed trading day from the cached 5-min bar
history (paper-trading-rebuild/shared/cache/bars/*.parquet, now 90 days
deep for Stan's current universe as of 2026-07-31's backfill), samples it
at 15-min intervals during market hours, and writes a plain data file for
the agentTurn replay cron to read.

Deliberately NOT an agentTurn step itself and Stan's replay cron does NOT
get exec/bash access -- this script does all the data extraction so the
replay session only ever needs read/write/wiki tools, never live data
fetches or trade-placing scripts. See skills/tick-replay-practice.md.

Progress is tracked in state/tick_replay_progress.json so each night
picks a fresh day instead of re-replaying the same one -- most recent
available day first, working backward. This is the default, single-day
mode: fresh $10k, no continuity across nights, optimized for variety of
judgment reps.

A second, chained mode (--session-id) exists alongside it for continuity:
walks FORWARD day-by-day through a state/backtest/<id>.db backtest
session (see scripts/backtest_session.py), carrying portfolio state
(cash + open positions) from one simulated day to the next so Stan can
practice genuinely holding a position across many replayed days/weeks,
not just intraday. Two-phase: this script only reserves the day
(start_day()) -- current_date doesn't advance until the replay skill
calls `backtest_session.py complete-day` after a successful journal
write, so a timed-out/errored fire retries the same day next time
instead of silently skipping it.

Usage:
    python3 scripts/prepare_tick_replay.py
    python3 scripts/prepare_tick_replay.py --session-id practice-chain-1 [--start-date 2026-05-01] [--end-date 2026-06-01] [--starting-capital 10000]
"""
import argparse
import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "scripts"))
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, "/home/openclaw/paper-trading-rebuild")
from sync_historical_bars import current_universe  # noqa: E402
import market_hours  # noqa: E402
import backtest_session  # noqa: E402
import trader_db  # noqa: E402
from src.counterfactual import UniverseSampler  # noqa: E402

CACHE_DIR = Path("/home/openclaw/paper-trading-rebuild/shared/cache/bars")
PROGRESS_PATH = WORKSPACE / "state" / "tick_replay_progress.json"
OUTPUT_PATH = WORKSPACE / "research" / "tick_replay_latest.json"
EXPERIMENTS_DIR = WORKSPACE / "state" / "experiments"
N_ALTERNATIVES_PER_TICKER = 3

TICK_TIMES = [
    dtime(hour, minute)
    for hour in range(9, 16)
    for minute in (0, 15, 30, 45)
    if (hour, minute) >= (9, 30) and (hour, minute) < (16, 0)
]


def _load_progress() -> dict:
    if PROGRESS_PATH.exists():
        return json.loads(PROGRESS_PATH.read_text())
    return {"replayed_dates": []}


def _save_progress(progress: dict) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(progress, indent=2))


MIN_SYMBOLS_PER_REPLAY_DAY = 5


def _available_dates(universe: list[str], reverse: bool = True) -> list[str]:
    """Trading days with cached data for a usable chunk of the universe,
    most recent first by default, weekends/holidays excluded (shouldn't
    have bars anyway, but belt-and-suspenders). Chained mode passes
    reverse=False to walk forward instead.

    A fixed floor, not a fraction of the universe: Stan's watchlist skews
    toward newly-added, thinly-traded small-caps that genuinely don't have
    deep Alpaca history yet, so most far-past days only have the handful
    of longer-tenured symbols cached -- that's real, not a bug, and a
    percentage-of-universe threshold would reject every older day."""
    date_counts: dict[str, int] = {}
    for symbol in universe:
        path = CACHE_DIR / f"{symbol}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path, columns=["timestamp"])
        except Exception:
            continue
        for d in pd.to_datetime(df["timestamp"], utc=True).dt.date.unique():
            date_counts[d.isoformat()] = date_counts.get(d.isoformat(), 0) + 1

    good_dates = [d for d, count in date_counts.items() if count >= MIN_SYMBOLS_PER_REPLAY_DAY]

    def is_real_trading_day(date_str: str) -> bool:
        d = datetime.fromisoformat(date_str).date()
        return d.weekday() < 5 and not market_hours.is_holiday(d)

    return sorted((d for d in good_dates if is_real_trading_day(d)), reverse=reverse)


def _next_chain_date(available_asc: list[str], manifest: dict) -> Optional[str]:
    """First cached trading day strictly after manifest['current_date'],
    or manifest['start_date'] itself if no day has completed yet. Respects
    end_date as a hard ceiling -- a chain never walks past its own end."""
    current = manifest.get("current_date")
    start = manifest["start_date"]
    end = manifest.get("end_date")
    for d in available_asc:
        if end and d > end:
            break
        if current is None:
            if d >= start:
                return d
        elif d > current:
            return d
    return None


def _portfolio_state(session_id: str) -> dict:
    """Cash + open positions from a backtest session's own DB, carried
    into the replay file so the agentTurn starts from 'here's what you're
    holding', same as live tick_prompt.md, instead of reconstructing it."""
    manifest = backtest_session.get_session(session_id)
    conn = trader_db.get_conn(Path(manifest["db_path"]))
    try:
        open_positions = trader_db.get_open_positions(conn)
    finally:
        conn.close()
    context = backtest_session.compute_context(session_id)
    return {
        "cash": context["cash"],
        "portfolio_value": context["portfolio_value"],
        "open_positions": [
            {
                "ticker": p["ticker"],
                "shares": p["shares"],
                "entry_price": p["entry_price"],
                "entry_time": p["entry_time"],
                "sector": p["sector"],
                "thesis": p["thesis"],
            }
            for p in open_positions
        ],
    }


def _snapshot_at(symbol: str, df: pd.DataFrame, as_of: datetime) -> dict | None:
    prior = df[df["timestamp"] <= as_of]
    if prior.empty:
        return None
    row = prior.iloc[-1]
    if pd.isna(row.get("rsi_14")) or pd.isna(row.get("macd_hist")):
        return None
    vol_window = prior["volume"].tail(20)
    vol_avg = vol_window.mean() if len(vol_window) > 0 else row["volume"]
    return {
        "close": float(row["close"]),
        "rsi_14": float(row["rsi_14"]),
        "macd_hist": float(row["macd_hist"]),
        "volume_ratio": float(row["volume"] / vol_avg) if vol_avg else 1.0,
    }


def _day_return(symbol: str, day) -> Optional[dict]:
    try:
        df = pd.read_parquet(CACHE_DIR / f"{symbol}.parquet")
    except Exception:
        return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    day_rows = df[df["timestamp"].dt.date == day]
    if len(day_rows) < 5:
        return None
    open_px = float(day_rows.iloc[0]["close"])
    close_px = float(day_rows.iloc[-1]["close"])
    if not open_px:
        return None
    return {"open": open_px, "close": close_px,
            "day_return_pct": round((close_px - open_px) / open_px * 100, 2)}


def _sample_alternatives(date_str: str, universe: list[str], n: int = N_ALTERNATIVES_PER_TICKER) -> dict:
    """Per-ticker price-band-matched peers Stan did NOT hold that day --
    'would ABC have beaten what you actually did with XYZ', not just a
    flat random list. Reuses UniverseSampler, the same price/volume-band
    matching the ML trainer's counterfactual training already relies on
    (src/counterfactual.py), rather than reinventing peer selection here."""
    day = datetime.fromisoformat(date_str).date()
    sampler = UniverseSampler(bars_dir=CACHE_DIR)

    alternatives: dict[str, dict] = {}
    for ref_symbol in universe:
        peers = sampler.sample_alternatives(ref_symbol, date_str, n=n)
        ref_stats = {}
        for peer in peers:
            stats = _day_return(peer, day)
            if stats is not None:
                ref_stats[peer] = stats
        if ref_stats:
            alternatives[ref_symbol] = ref_stats
    return alternatives


def build_replay_file(date_str: str, universe: list[str]) -> dict:
    frames = {}
    for symbol in universe:
        path = CACHE_DIR / f"{symbol}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        frames[symbol] = df

    day = datetime.fromisoformat(date_str).date()
    et = market_hours.ET

    ticks = []
    for t in TICK_TIMES:
        as_of = datetime.combine(day, t, tzinfo=et)
        snapshot = {}
        for symbol, df in frames.items():
            snap = _snapshot_at(symbol, df, as_of)
            if snap is not None:
                snapshot[symbol] = snap
        if snapshot:
            ticks.append({"time": t.strftime("%H:%M"), "snapshot": snapshot})

    eod_closes = {}
    close_cutoff = datetime.combine(day, dtime(16, 0), tzinfo=et)
    for symbol, df in frames.items():
        prior = df[df["timestamp"] <= close_cutoff]
        if not prior.empty:
            eod_closes[symbol] = float(prior.iloc[-1]["close"])

    return {
        "date": date_str,
        "universe": sorted(frames.keys()),
        "ticks": ticks,
        "actual_eod_closes": eod_closes,
        "alternative_candidates": _sample_alternatives(date_str, universe=sorted(frames.keys())),
        "prepared_at": datetime.now(et).isoformat(),
    }


def _main_single_day(universe: list[str]) -> int:
    progress = _load_progress()
    replayed = set(progress.get("replayed_dates", []))
    candidates = [d for d in _available_dates(universe) if d not in replayed]

    if not candidates:
        print(json.dumps({"status": "skipped", "reason": "no unreplayed trading days available yet"}))
        return 0

    date_str = candidates[0]
    result = build_replay_file(date_str, universe)

    if len(result["ticks"]) < 5:
        print(json.dumps({"status": "skipped", "reason": f"too few usable ticks ({len(result['ticks'])}) for {date_str}"}))
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2))

    progress.setdefault("replayed_dates", []).append(date_str)
    _save_progress(progress)

    print(json.dumps({
        "status": "ok", "mode": "single-day", "date": date_str, "n_ticks": len(result["ticks"]),
        "n_symbols": len(result["universe"]), "output": str(OUTPUT_PATH),
    }))
    return 0


def _main_chained(session_id: str, universe: list[str], start_date: Optional[str],
                   end_date: Optional[str], starting_capital: float) -> int:
    manifest = backtest_session.get_session(session_id)
    available_asc = _available_dates(universe, reverse=False)

    if manifest is None:
        if not available_asc:
            print(json.dumps({"status": "skipped", "reason": "no cached trading days available yet to start a session"}))
            return 0
        manifest = backtest_session.create_session(
            session_id,
            start_date=start_date or available_asc[0],
            end_date=end_date,
            starting_capital=starting_capital,
        )

    if manifest["status"] != "active":
        print(json.dumps({
            "status": "skipped",
            "reason": f"session {session_id!r} is {manifest['status']!r}, not active -- use a new --session-id",
        }))
        return 0

    # Retry semantics: a day left in-progress by a prior fire that never
    # completed (timeout/error) gets replayed again, not skipped -- see
    # backtest_session.py's two-phase start_day()/complete_day() docstring.
    if manifest.get("day_in_progress"):
        date_str = manifest["day_in_progress"]
    else:
        date_str = _next_chain_date(available_asc, manifest)
        if date_str is None:
            print(json.dumps({
                "status": "skipped",
                "reason": f"session {session_id!r} chain exhausted -- no further cached trading days "
                          f"after {manifest.get('current_date') or manifest['start_date']}",
            }))
            return 0
        backtest_session.start_day(session_id, date_str)

    result = build_replay_file(date_str, universe)

    if len(result["ticks"]) < 5:
        # day_in_progress stays set on purpose -- next fire retries this
        # same date rather than silently skipping a thin day.
        print(json.dumps({
            "status": "skipped", "session_id": session_id, "date": date_str,
            "reason": f"too few usable ticks ({len(result['ticks'])}) for {date_str}",
        }))
        return 0

    result["mode"] = "chained"
    result["session_id"] = session_id
    result["portfolio"] = _portfolio_state(session_id)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2))

    print(json.dumps({
        "status": "ok", "mode": "chained", "session_id": session_id, "date": date_str,
        "n_ticks": len(result["ticks"]), "n_symbols": len(result["universe"]), "output": str(OUTPUT_PATH),
    }))
    return 0


def _main_experiment(experiment_id: str, date_str: str, universe: list[str], params: dict, run_id: str) -> int:
    """Explicit-date, non-continuity, freely repeatable replay for controlled
    single-variable experiments (e.g. a signal-scorecard override), as opposed
    to single-day mode's auto-picked variety or chained mode's forward-walking
    continuity. Deliberately never touches state/tick_replay_progress.json --
    that file exists to stop single-day practice from repeating a date, which
    is the exact opposite of what a controlled experiment needs to do.

    `params` is opaque here -- this script doesn't interpret it, just passes
    it through into the output file. It's the replay skill/agent's job to
    apply it (e.g. merge into the real signal scorecard before calling
    reconcile_signals) and to log the outcome via scripts/experiment_log.py,
    which is what actually makes separate runs comparable afterward."""
    if date_str not in _available_dates(universe):
        print(json.dumps({
            "status": "skipped",
            "reason": f"{date_str} not in cached universe -- not enough symbols with data that day",
        }))
        return 0

    result = build_replay_file(date_str, universe)
    if len(result["ticks"]) < 5:
        print(json.dumps({
            "status": "skipped",
            "reason": f"too few usable ticks ({len(result['ticks'])}) for {date_str}",
        }))
        return 0

    result["mode"] = "experiment"
    result["experiment_id"] = experiment_id
    result["run_id"] = run_id
    result["params"] = params

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2))

    print(json.dumps({
        "status": "ok", "mode": "experiment", "experiment_id": experiment_id, "run_id": run_id,
        "date": date_str, "params": params, "n_ticks": len(result["ticks"]),
        "n_symbols": len(result["universe"]), "output": str(OUTPUT_PATH),
    }))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session-id", default=None,
                         help="Chained multi-day mode: walk forward through a backtest_session.py "
                              "session, carrying portfolio state day to day. Creates the session if "
                              "it doesn't exist yet. Omit for the default single-day mode.")
    parser.add_argument("--start-date", default=None,
                         help="Only consulted when --session-id creates a brand-new session; "
                              "defaults to the earliest cached trading day available.")
    parser.add_argument("--end-date", default=None,
                         help="Only consulted when --session-id creates a brand-new session.")
    parser.add_argument("--starting-capital", type=float, default=backtest_session.STARTING_CASH_DEFAULT,
                         help="Only consulted when --session-id creates a brand-new session.")
    parser.add_argument("--experiment-id", default=None,
                         help="Controlled-experiment mode: explicit-date, non-continuity, freely "
                              "repeatable replay for testing one varied parameter at a time (e.g. a "
                              "signal-scorecard override) against an identical historical day. "
                              "Requires --date. Unlike single-day mode, never marks the date as "
                              "replayed, since experiments are meant to reuse the same date.")
    parser.add_argument("--date", default=None,
                         help="Required with --experiment-id: the exact historical date to replay "
                              "(YYYY-MM-DD), reusable across as many runs as needed.")
    parser.add_argument("--params", default="{}",
                         help="JSON object describing what this run varies. Opaque to this script -- "
                              "passed through into the output file for the replay skill/agent to "
                              "apply and log via scripts/experiment_log.py.")
    args = parser.parse_args(argv)

    universe = current_universe()
    if not universe:
        print(json.dumps({"status": "skipped", "reason": "empty universe"}))
        return 0

    if args.experiment_id:
        if not args.date:
            print(json.dumps({"status": "error", "reason": "--experiment-id requires --date"}))
            return 1
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "error", "reason": f"--params is not valid JSON: {e}"}))
            return 1
        run_id = datetime.now(market_hours.ET).strftime("%Y%m%dT%H%M%S")
        return _main_experiment(args.experiment_id, args.date, universe, params, run_id)
    if args.session_id:
        return _main_chained(args.session_id, universe, args.start_date, args.end_date, args.starting_capital)
    return _main_single_day(universe)


if __name__ == "__main__":
    sys.exit(main())
