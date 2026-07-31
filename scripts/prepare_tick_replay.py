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
available day first, working backward.

Usage:
    python3 scripts/prepare_tick_replay.py
"""
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
from src.counterfactual import UniverseSampler  # noqa: E402

CACHE_DIR = Path("/home/openclaw/paper-trading-rebuild/shared/cache/bars")
PROGRESS_PATH = WORKSPACE / "state" / "tick_replay_progress.json"
OUTPUT_PATH = WORKSPACE / "research" / "tick_replay_latest.json"
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


def _available_dates(universe: list[str]) -> list[str]:
    """Trading days with cached data for a usable chunk of the universe,
    most recent first, weekends/holidays excluded (shouldn't have bars
    anyway, but belt-and-suspenders).

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

    return sorted((d for d in good_dates if is_real_trading_day(d)), reverse=True)


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


def main() -> int:
    universe = current_universe()
    if not universe:
        print(json.dumps({"status": "skipped", "reason": "empty universe"}))
        return 0

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
        "status": "ok", "date": date_str, "n_ticks": len(result["ticks"]),
        "n_symbols": len(result["universe"]), "output": str(OUTPUT_PATH),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
