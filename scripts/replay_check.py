#!/usr/bin/env python3
"""
Off-hours replay check — the actual "would the new hardened rule have
helped" test that was missing from the nightly evolve step. Runs the SAME
base entry strategy through the historical replay harness across three
variants (v1.0 / v1.1 / v1.2), isolating exactly what each rule change did,
instead of trusting the promotion on narrative confidence alone.

Uses paper-trading-rebuild's src/replay.py ReplayHarness (imported
directly — no repo-specific deps in that module). Historical daily bars
come straight from Alpaca (market_data.bars_1d only has real history for
NVDA among Stonks's holdings — everything else is empty there), same
approach as the Trading Terminal's indicators.py.

v1.2 coverage: RSI-exhaustion hard exit, 5-day time-stop, and
profit-target partial-trim (25%) are implemented faithfully — they only
need price/RSI/MACD data, which this script already fetches. Entry sizing
uses a real triple-confirmation check (RSI>55 rising, MACD>signal and
positive, price>MA20>MA50). NOT implemented: sector veto and VIX-tiered
sizing (no sector/VIX data fetched here) and the fundamentals quality gate
(Alpaca bars API has no debt/FCF data) — v1.2's result should be read as
"the part of v1.2 that's testable on price data alone", not the full rule
set.

2026-07-21 fix: the v1.0/v1.1 code previously read pandas_ta's MACD output
as `macd_df.iloc[:, 2]` and called it "macd_hist" — but pandas_ta's column
order is [MACD, MACDh, MACDs], so index 2 is the SIGNAL line, not the
histogram (index 1). The "MACDh flip" exit had actually been testing
signal-line sign flips, a related but different signal. Fixed here — all
three MACD components are now stored correctly and separately.

2026-07-22: strategy.md v1.3 reverted entry/exit logic to be identical to
this harness's "v1.0" variant (simple RSI 45-65 momentum entry, fixed
stop-loss/profit-target full exit) — so "v1.0" here IS what's currently
live, not a retired baseline. v1.1/v1.2 remain in the comparison as
already-tried-and-reverted alternatives, not candidates for promotion
without new evidence (see strategy.md Version History).

Usage:
    python3 scripts/replay_check.py [TICKER ...]

With no TICKER args, the universe is built from what's actually live:
every currently-open position (positions/*.md) plus every active (not
struck-through) watchlist candidate (strategies/watchlist.md). This
mirrors real trading activity instead of a hand-picked ticker list, so
the comparison can't be accidentally cherry-picked.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_ta as ta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

sys.path.insert(0, "/home/openclaw/paper-trading-rebuild")
from src.replay import Tick, TraderDecision, replay_trader  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
# Only used if load_live_universe() finds nothing (e.g. fresh checkout with
# no open positions and an empty watchlist) — not the primary source.
FALLBACK_TICKERS = ["CHWY", "F", "FUBO", "GME", "KHC", "LYFT", "MVST", "NVDA", "SOFI"]

LOOKBACK_DAYS = 200
RSI_LENGTH = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
CHOPPY_VOL_THRESHOLD = 0.035  # 20d rolling stdev of daily returns

# Mirrors params.json's current values.
STOP_LOSS_PCT = -10.0
PROFIT_TARGET_PCT = 12.0
RSI_EXHAUSTION_EXIT = 75.0          # exit_rules.rsi_exhaustion_hard_exit
MAX_HOLDING_DAYS = 5                # risk_guards.max_holding_days
PROFIT_TRIM_PCT = 0.25              # trim.profit_target_trim_pct (partial, not full close)

# v1.6 (2026-07-24): no position-count cap, scale-into-winners is a real
# decision path. These are rule-based proxies for what's genuine LLM
# judgment live ("thesis intact, real momentum") — same caveat as v1.2's
# triple-confirmation entry being a proxy for qualitative confirmation.
# v1.7 (2026-07-24, same day): reconsider every tick it still qualifies —
# no cooldown/pacing (removed SCALE_IN_MIN_GAP_DAYS) — and size scales
# with how strong the winner actually is, not a fixed increment.
# max_position_pct remains the hard backstop regardless of how aggressive
# the add gets.
SCALE_IN_MIN_PNL_PCT = 3.0           # only a REAL winner, not noise, qualifies at all
SCALE_IN_SIZE_FACTOR = 0.5           # base add size at exactly the threshold — scales up from here
SCALE_IN_MAX_MULTIPLE = 3.0          # cap on how far magnitude-scaling can push the add size
LEGACY_MAX_POSITIONS = 25            # the cap that was live before v1.6 removed it
ENTRY_CONVICTION = 0.6               # matches v1.0/v1.1's fresh-entry conviction below
MAX_POSITION_PCT = 0.06              # mirrors params.json risk.max_position_pct; also what
                                      # run_variants()/sweep_thresholds() pass the harness


def load_live_universe():
    """Build the ticker universe from what's actually live right now:
    every open position plus every active watchlist candidate. Falls back
    to FALLBACK_TICKERS if both are empty. Migrated 2026-07-28 from
    globbing positions/*.md + regex-parsing watchlist.md to querying
    trader_db.py directly."""
    conn = trader_db.get_conn()
    try:
        tickers = {p["ticker"] for p in trader_db.get_open_positions(conn)}
        tickers |= {c["ticker"] for c in trader_db.get_watchlist_candidates(conn)}
    finally:
        conn.close()

    return sorted(tickers) if tickers else list(FALLBACK_TICKERS)


def fetch_history(tickers):
    key = os.environ.get("ALPACA_STONKS_KEY")
    secret = os.environ.get("ALPACA_STONKS_SECRET")
    if not key or not secret:
        print(json.dumps({"error": "ALPACA_STONKS_KEY/SECRET not set"}))
        sys.exit(1)

    client = StockHistoricalDataClient(key, secret)
    now = pd.Timestamp.now(tz="America/New_York")
    start = now - pd.Timedelta(days=LOOKBACK_DAYS)
    req = StockBarsRequest(
        symbol_or_symbols=tickers,
        timeframe=TimeFrame(1, TimeFrameUnit.Day),
        start=start.isoformat(), end=now.isoformat(), feed=DataFeed.IEX,
    )
    bars = client.get_stock_bars(req)

    frames = {}
    for sym in tickers:
        rows = bars.data.get(sym, [])
        if len(rows) < 40:
            continue
        df = pd.DataFrame([{
            "timestamp": r.timestamp, "close": float(r.close),
            "high": float(r.high), "low": float(r.low), "volume": r.volume,
        } for r in rows])
        df["rsi_14"] = ta.rsi(df["close"], length=RSI_LENGTH)
        macd_df = ta.macd(df["close"], fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
        # pandas_ta column order: [MACD, MACDh, MACDs] — histogram is index 1, not 2.
        df["macd_line"] = macd_df.iloc[:, 0]
        df["macd_hist"] = macd_df.iloc[:, 1]
        df["macd_signal"] = macd_df.iloc[:, 2]
        df["ma20"] = df["close"].rolling(20).mean()
        df["ma50"] = df["close"].rolling(50).mean()
        df["daily_return"] = df["close"].pct_change()
        df["vol_20d"] = df["daily_return"].rolling(20).std()
        # Share-volume 20d average — not in the dropna subset (same as
        # ma20/ma50), so it's NaN for the first ~20 rows but doesn't
        # shrink the usable window. Added 2026-07-23: a raw share count
        # alone isn't decision-useful without a baseline to compare
        # against — llm_replay.py's entry decisions were citing "no
        # volume data" despite raw volume being fetched, since nothing
        # gave it context for what's normal.
        df["volume_ma20"] = df["volume"].rolling(20).mean()
        frames[sym] = df.dropna(subset=["rsi_14", "macd_hist", "vol_20d"]).reset_index(drop=True)
    return frames


def build_tick_stream(frames):
    """Chronological, cross-ticker stream of Ticks — replay.py iterates
    one ticker-day at a time, interleaved by timestamp."""
    ticks = []
    for sym, df in frames.items():
        for _, row in df.iterrows():
            ticks.append(Tick(
                timestamp=row["timestamp"], ticker=sym, open=row["close"],
                high=row["high"], low=row["low"], close=row["close"],
                volume=int(row["volume"]), rsi=float(row["rsi_14"]),
            ))
    ticks.sort(key=lambda t: t.timestamp)
    return ticks


def make_trader(frames, variant, stop_loss_pct=None, profit_target_pct=None,
                 max_positions=None, scale_into_winners=False, scale_in_max_per_day=None,
                 scale_in_max_multiple=None):
    """Build a trader function closing over per-ticker indicator lookups
    (Tick only carries rsi) and a small amount of flip-detection state.

    variant: "v1.0" | "v1.1" | "v1.2"
    stop_loss_pct/profit_target_pct: override the module defaults (used by
    sweep_thresholds() to test candidate values) — None means "use the
    current live params.json-mirroring constants," same behavior as before
    this was parameterized.
    max_positions: cap on distinct held tickers before a NEW-ticker BUY is
    blocked (adding to an existing position is never blocked by this,
    mirrors executor.py's gate_max_positions). None = uncapped (v1.6+, live
    today). Every variant before v1.6 was tested here with this implicitly
    unset — the harness never modeled a cap at all, not just "capped at 25".
    scale_into_winners: v1.6/v1.7's other new behavior — when True, a held
    position with intact momentum and real (not noise) PnL gets an add-on
    BUY instead of a silent HOLD, gated only by SCALE_IN_MIN_PNL_PCT, sized
    up with magnitude (bigger winner = bigger add, capped at
    SCALE_IN_MAX_MULTIPLE) rather than a fixed increment.
    scale_in_max_per_day: v1.7 as shipped reconsiders every tick with no
    pacing at all. This is a separate test axis (2026-07-24, same-day
    follow-up) isolating whether it's the *frequency* or the *magnitude
    scaling* that hurt v1.7's backtest — None (default) keeps v1.7's actual
    every-tick behavior; an int caps scale-ins to that many calendar days
    (not ticks) per ticker. Confirmed a no-op on this daily-bar harness
    (every variant already gets at most one tick per ticker per day) — kept
    for completeness/documentation, not because it changes results here.
    scale_in_max_multiple: overrides the module-level SCALE_IN_MAX_MULTIPLE
    for this trader instance — a second same-day follow-up test axis,
    isolating whether a gentler size-scaling cap recovers some of v1.6's
    better Sharpe while still rewarding bigger winners more than a flat
    size would. None (default) uses the module constant (3.0, v1.7's
    shipped value).
    """
    assert variant in ("v1.0", "v1.1", "v1.2")
    stop_loss_pct = STOP_LOSS_PCT if stop_loss_pct is None else stop_loss_pct
    profit_target_pct = PROFIT_TARGET_PCT if profit_target_pct is None else profit_target_pct
    scale_in_max_multiple = SCALE_IN_MAX_MULTIPLE if scale_in_max_multiple is None else scale_in_max_multiple
    lookup = {}
    for sym, df in frames.items():
        for _, row in df.iterrows():
            lookup[(sym, row["timestamp"])] = row

    last_scale_in_date = {}
    prev_hist = {}
    prev_rsi = {}

    def trader(tick, portfolio):
        row = lookup.get((tick.ticker, tick.timestamp))
        macd_hist = float(row["macd_hist"]) if row is not None else None
        macd_line = float(row["macd_line"]) if row is not None else None
        macd_signal = float(row["macd_signal"]) if row is not None else None
        vol_20d = float(row["vol_20d"]) if row is not None else None
        ma20 = float(row["ma20"]) if row is not None and pd.notna(row["ma20"]) else None
        ma50 = float(row["ma50"]) if row is not None and pd.notna(row["ma50"]) else None
        held = portfolio.positions.get(tick.ticker)

        last_hist = prev_hist.get(tick.ticker)
        last_rsi = prev_rsi.get(tick.ticker)
        if macd_hist is not None:
            prev_hist[tick.ticker] = macd_hist
        if tick.rsi is not None:
            prev_rsi[tick.ticker] = tick.rsi

        if held is not None:
            pnl_pct = (tick.close - held.entry_price) / held.entry_price * 100
            held_days = (tick.timestamp - held.entry_time).days

            if variant in ("v1.1", "v1.2") and macd_hist is not None and last_hist is not None:
                if last_hist > 0 and macd_hist < 0:
                    return TraderDecision(ticker=tick.ticker, decision="SELL",
                                           conviction=1.0, rationale=f"{variant}: MACDh flip positive->negative")

            if variant == "v1.2" and tick.rsi is not None and tick.rsi >= RSI_EXHAUSTION_EXIT:
                # kairos: absolute override, not negotiated by MACD
                return TraderDecision(ticker=tick.ticker, decision="SELL",
                                       conviction=1.0, rationale=f"v1.2: RSI exhaustion {tick.rsi:.1f} >= {RSI_EXHAUSTION_EXIT}")

            if variant == "v1.2" and held_days >= MAX_HOLDING_DAYS:
                return TraderDecision(ticker=tick.ticker, decision="SELL",
                                       conviction=1.0, rationale=f"v1.2: time-stop, held {held_days}d >= {MAX_HOLDING_DAYS}d")

            if pnl_pct <= stop_loss_pct:
                return TraderDecision(ticker=tick.ticker, decision="SELL",
                                       conviction=1.0, rationale="stop_loss_pct breached")

            if pnl_pct >= profit_target_pct:
                if variant == "v1.2":
                    trim_shares = max(1, int(held.shares * PROFIT_TRIM_PCT))
                    return TraderDecision(ticker=tick.ticker, decision="SELL", conviction=1.0,
                                           shares=trim_shares,
                                           rationale=f"v1.2: profit_target_trim_pct, sell {trim_shares}/{held.shares}")
                return TraderDecision(ticker=tick.ticker, decision="SELL",
                                       conviction=1.0, rationale="profit_target_pct reached (full exit)")

            if scale_into_winners and tick.rsi is not None and pnl_pct >= SCALE_IN_MIN_PNL_PCT:
                still_in_band = 45 < tick.rsi < 65
                choppy_blocked = variant == "v1.1" and vol_20d is not None and vol_20d > CHOPPY_VOL_THRESHOLD
                today = tick.timestamp.date() if hasattr(tick.timestamp, "date") else tick.timestamp
                paced_out = (scale_in_max_per_day is not None
                             and last_scale_in_date.get(tick.ticker) == today)
                if still_in_band and not choppy_blocked and not paced_out:
                    # v1.7 as shipped: no cooldown, reconsidered every tick it
                    # still qualifies. scale_in_max_per_day (default None) is a
                    # separate test axis, not v1.7's live behavior — caps to
                    # one scale-in per calendar day per ticker when set.
                    # Explicit shares, not a lower conviction, express sizing —
                    # conviction below require_conviction (0.5, set by
                    # run_variants()/sweep_thresholds()) would get silently
                    # rejected by the harness's own gate before ever reaching
                    # _execute_buy. conviction stays at the same level as a fresh
                    # entry so it clears that gate; size_multiple (bigger winner
                    # = bigger add, capped at SCALE_IN_MAX_MULTIPLE) carries the
                    # actual sizing signal instead.
                    last_scale_in_date[tick.ticker] = today
                    size_multiple = min(scale_in_max_multiple, pnl_pct / SCALE_IN_MIN_PNL_PCT)
                    add_shares = max(1, int(
                        (portfolio.total_equity * MAX_POSITION_PCT * ENTRY_CONVICTION
                         * SCALE_IN_SIZE_FACTOR * size_multiple) / tick.close
                    ))
                    return TraderDecision(ticker=tick.ticker, decision="BUY", conviction=ENTRY_CONVICTION,
                                           shares=add_shares,
                                           rationale=f"v1.7: scale into winner, +{pnl_pct:.1f}% pnl, "
                                                     f"{size_multiple:.1f}x base size, RSI still in-band")
            return TraderDecision(ticker=tick.ticker, decision="HOLD", conviction=0.0)

        if max_positions is not None and portfolio.position_count >= max_positions:
            return TraderDecision(ticker=tick.ticker, decision="HOLD", conviction=0.0,
                                   rationale=f"at {max_positions}-position cap, no new tickers")

        # ── Entry ──
        if variant == "v1.2":
            # Triple confirmation: rsi_gt_55_rising, macd_gt_signal_and_positive, price_gt_ma20_ma50.
            # 2-of-3 = half-size probe, 3-of-3 = full size. Sector veto / VIX-tiering
            # not implemented (no sector or VIX data fetched here).
            rsi_rising = tick.rsi is not None and last_rsi is not None and tick.rsi > 55 and tick.rsi > last_rsi
            macd_confirm = macd_line is not None and macd_signal is not None and macd_line > macd_signal and macd_line > 0
            price_confirm = ma20 is not None and ma50 is not None and tick.close > ma20 > ma50
            confirmations = sum([rsi_rising, macd_confirm, price_confirm])

            if confirmations >= 2:
                conviction = 0.9 if confirmations == 3 else 0.5
                return TraderDecision(ticker=tick.ticker, decision="BUY", conviction=conviction,
                                       rationale=f"v1.2: triple confirmation {confirmations}/3 "
                                                 f"(rsi_rising={rsi_rising}, macd={macd_confirm}, price={price_confirm})")
            return TraderDecision(ticker=tick.ticker, decision="HOLD", conviction=0.0)

        # v1.0 / v1.1 share the same simple momentum entry — only exit/gating differs.
        if tick.rsi is not None and 45 < tick.rsi < 65:
            if variant == "v1.1" and vol_20d is not None and vol_20d > CHOPPY_VOL_THRESHOLD:
                return TraderDecision(ticker=tick.ticker, decision="HOLD", conviction=0.0,
                                       rationale="v1.1: regime-gated, choppy/high-vol")
            return TraderDecision(ticker=tick.ticker, decision="BUY", conviction=0.6,
                                   rationale="momentum entry: RSI 45-65")
        return TraderDecision(ticker=tick.ticker, decision="HOLD", conviction=0.0)

    return trader


# Addressable by name so other scripts (e.g. universe_scan.py) can build a
# trader for a given strategy without knowing make_trader's internal
# variant-dispatch details. Same closures as before — no behavior change.
STRATEGY_BUILDERS = {
    "v1.0": lambda frames: make_trader(frames, "v1.0"),
    "v1.1": lambda frames: make_trader(frames, "v1.1"),
    "v1.2": lambda frames: make_trader(frames, "v1.2"),
    "v1.1-capped25": lambda frames: make_trader(frames, "v1.1", max_positions=LEGACY_MAX_POSITIONS),
    "v1.7": lambda frames: make_trader(frames, "v1.1", scale_into_winners=True),
    "v1.7-daily": lambda frames: make_trader(frames, "v1.1", scale_into_winners=True, scale_in_max_per_day=1),
    "v1.7-gentle": lambda frames: make_trader(frames, "v1.1", scale_into_winners=True, scale_in_max_multiple=1.5),
}


TRADING_DAYS_PER_YEAR = 252


def resample_to_daily_equity(result):
    """Collapse (ticker, tick) equity samples down to one value per calendar
    day (the last observation on that day) before computing return-based
    metrics. Without this, Sharpe/Sortino would be computed over an
    oversampled series — result.equity_curve has one entry per (ticker,
    tick) pair in this multi-ticker interleaved replay, not one per day, so
    a naive per-tick std/mean would badly understate volatility relative to
    a real daily portfolio return series.
    """
    if not result.timestamps or len(result.timestamps) != len(result.equity_curve):
        return None  # older ReplayResult without timestamps, or empty run

    daily = {}
    for ts, equity in zip(result.timestamps, result.equity_curve):
        day = ts.date() if hasattr(ts, "date") else ts
        daily[day] = float(equity)  # last write per day wins, ticks are chronological
    days = sorted(daily.keys())
    return [daily[d] for d in days]


def compute_risk_metrics(result, risk_free_rate: float = 0.0) -> dict:
    """Sharpe, Sortino, Calmar, and max drawdown from a resampled daily
    equity series. Returns None values with a note if there isn't enough
    daily history to compute anything meaningful (need 2+ days).
    """
    daily_equity = resample_to_daily_equity(result)
    if daily_equity is None or len(daily_equity) < 2:
        return {
            "sharpe": None, "sortino": None, "calmar": None,
            "max_drawdown_pct": None,
            "note": "insufficient daily history for risk metrics (need timestamps + 2+ trading days)",
        }

    equity = np.array(daily_equity, dtype=np.float64)
    daily_returns = np.diff(equity) / equity[:-1]

    # Max drawdown — largest peak-to-trough decline in the equity curve.
    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max
    max_dd_pct = float(drawdowns.min() * 100)  # negative number, e.g. -12.4

    mean_daily = daily_returns.mean()
    std_daily = daily_returns.std(ddof=1) if len(daily_returns) > 1 else 0.0
    sharpe = (
        (mean_daily - risk_free_rate / TRADING_DAYS_PER_YEAR) / std_daily * np.sqrt(TRADING_DAYS_PER_YEAR)
        if std_daily > 0 else None
    )

    downside_returns = daily_returns[daily_returns < 0]
    downside_std = downside_returns.std(ddof=1) if len(downside_returns) > 1 else 0.0
    sortino = (
        (mean_daily - risk_free_rate / TRADING_DAYS_PER_YEAR) / downside_std * np.sqrt(TRADING_DAYS_PER_YEAR)
        if downside_std > 0 else None
    )

    n_days = len(daily_equity)
    total_return = (equity[-1] - equity[0]) / equity[0]
    annualized_return = (1 + total_return) ** (TRADING_DAYS_PER_YEAR / n_days) - 1 if n_days > 0 else 0.0
    calmar = annualized_return / abs(max_dd_pct / 100) if max_dd_pct < 0 else None

    return {
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sortino": round(sortino, 3) if sortino is not None else None,
        "calmar": round(calmar, 3) if calmar is not None else None,
        "max_drawdown_pct": round(max_dd_pct, 2),
        "trading_days": n_days,
    }


def summarize(result, label):
    return {
        "variant": label,
        "n_ticks": result.n_ticks,
        "n_decisions": result.n_decisions,
        "n_trades": len(result.trades),
        "win_rate": round(result.win_rate, 3),
        "total_return_pct": round(result.total_return_pct, 3),
        "total_pnl": round(result.total_pnl, 2),
        "final_equity": round(result.final_equity, 2),
        **compute_risk_metrics(result),
    }


def split_ticks_by_midpoint(ticks):
    """Split a chronological tick stream into two halves by DATE midpoint
    (not by tick/index count, which would skew toward whichever half has
    more multi-ticker density). Indicators (RSI/MACD/MA) were already
    computed on the full fetched history before this split, so the second
    half doesn't have a cold-start problem — only which ticks the trader
    gets to act on changes, not the indicator values themselves.
    """
    if not ticks:
        return [], []
    dates = sorted({t.timestamp for t in ticks})
    mid_date = dates[len(dates) // 2]
    first_half = [t for t in ticks if t.timestamp < mid_date]
    second_half = [t for t in ticks if t.timestamp >= mid_date]
    return first_half, second_half


def run_variants(frames, ticks):
    """Run every registered strategy over a given tick stream (a full
    window or a half-window slice), reusing the same frames dict (and
    therefore the same indicator lookups) regardless of which ticks are
    actually being iterated."""
    results = {}
    for variant, build_trader in STRATEGY_BUILDERS.items():
        trader_fn = build_trader(frames)
        results[variant] = replay_trader(ticks, trader_fn, initial_balance=10_000.0,
                                          max_position_pct=0.06, require_conviction=0.5)
    return results


VARIANT_LABELS = {
    "v1.0": "baseline: fixed stop/target only, simple RSI 45-65 entry",
    "v1.1": "+ MACDh-flip exit, + regime-gated entries",
    "v1.2": "+ RSI-exhaustion exit, + time-stop, + profit trim, "
            "+ triple-confirmation entry (sector veto/VIX-tiering/quality-gate NOT modeled)",
    "v1.1-capped25": "v1.1 rules + max_positions=25 (the cap v1.5-and-earlier had, before v1.6 removed it — "
                      "isolates whether the old cap actually constrained anything on this universe)",
    "v1.7": "== live strategy.md v1.7: v1.1 rules (regime-gated entry, MACDh-flip exit) + "
            "no position cap + scale-into-winners, no pacing, magnitude-scaled sizing "
            "(rule-based proxy: +3% pnl, RSI still in-band, reconsidered every tick, size scales "
            "with pnl magnitude up to SCALE_IN_MAX_MULTIPLE — real thing is LLM judgment, "
            "see SCALE_IN_* constants)",
    "v1.7-daily": "same as v1.7 but scale_in_max_per_day=1 — this harness replays DAILY bars "
                  "(one tick per ticker per day for every variant), so this is expected to be a "
                  "no-op vs v1.7 here, not a real test of intraday pacing (see module docstring)",
    "v1.7-gentle": "same as v1.7 but scale_in_max_multiple=1.5 instead of 3.0 — tests whether a "
                   "gentler size-scaling cap recovers some of v1.6's better Sharpe",
}


def sweep_thresholds(frames, ticks, stop_loss_grid, profit_target_grid, variant="v1.0"):
    """Small grid sweep over stop_loss_pct/profit_target_pct for a given
    strategy variant (default v1.0, the live strategy's entry/exit logic).
    For each combo, runs the SAME both-halves split-window robustness check
    used to decide the v1.1 promotion (2026-07-23) — Sharpe positive in
    BOTH halves, not just a single aggregate number — rather than trusting
    one full-window backtest. Only sweeps the two thresholds the harness
    actually models; trailing_stop_pct isn't simulated here at all (a
    separate, real gap — the harness only ever tested fixed full-exit
    stops, never the trailing mechanism live in executor.py).

    Returns candidates sorted by full-window Sharpe (robust ones first),
    each with "robust": bool (Sharpe > 0 in both halves).
    """
    first_half, second_half = split_ticks_by_midpoint(ticks)
    candidates = []
    for stop_loss_pct in stop_loss_grid:
        for profit_target_pct in profit_target_grid:
            def build_trader(frames, sl=stop_loss_pct, pt=profit_target_pct):
                return make_trader(frames, variant, stop_loss_pct=sl, profit_target_pct=pt)

            full_result = replay_trader(ticks, build_trader(frames), initial_balance=10_000.0,
                                         max_position_pct=0.06, require_conviction=0.5)
            first_result = replay_trader(first_half, build_trader(frames), initial_balance=10_000.0,
                                          max_position_pct=0.06, require_conviction=0.5)
            second_result = replay_trader(second_half, build_trader(frames), initial_balance=10_000.0,
                                           max_position_pct=0.06, require_conviction=0.5)

            full_summary = summarize(full_result, variant)
            first_sharpe = compute_risk_metrics(first_result)["sharpe"]
            second_sharpe = compute_risk_metrics(second_result)["sharpe"]
            robust = bool(first_sharpe is not None and second_sharpe is not None
                          and first_sharpe > 0 and second_sharpe > 0)

            candidates.append({
                "stop_loss_pct": float(stop_loss_pct),
                "profit_target_pct": float(profit_target_pct),
                "sharpe": full_summary["sharpe"],
                "first_half_sharpe": None if first_sharpe is None else float(first_sharpe),
                "second_half_sharpe": None if second_sharpe is None else float(second_sharpe),
                "robust": robust,
                "total_return_pct": full_summary["total_return_pct"],
                "n_trades": full_summary["n_trades"],
            })

    def sort_key(c):
        return (c["robust"], c["sharpe"] if c["sharpe"] is not None else -999)

    candidates.sort(key=sort_key, reverse=True)
    return candidates


# Default sweep grid — small enough to stay fast (a few seconds on the
# live universe), wide enough to span meaningfully tighter/looser than the
# current live thresholds (params.json risk.stop_loss_pct=-10.0,
# profit_target_pct=12.0).
DEFAULT_STOP_LOSS_GRID = [-15.0, -12.0, -10.0, -8.0, -6.0]
DEFAULT_PROFIT_TARGET_GRID = [8.0, 10.0, 12.0, 15.0, 18.0]


def main():
    args = sys.argv[1:]
    split_window = "--split-window" in args
    sweep = "--sweep" in args
    tickers = [a for a in args if a not in ("--split-window", "--sweep")]
    if not tickers:
        tickers = load_live_universe()

    frames = fetch_history(tickers)
    if not frames:
        print(json.dumps({"error": "no usable history for any ticker"}))
        return

    ticks = build_tick_stream(frames)

    if sweep:
        candidates = sweep_thresholds(frames, ticks, DEFAULT_STOP_LOSS_GRID, DEFAULT_PROFIT_TARGET_GRID)
        print(json.dumps({
            "tickers_used": sorted(frames.keys()),
            "lookback_days": LOOKBACK_DAYS,
            "stop_loss_grid": DEFAULT_STOP_LOSS_GRID,
            "profit_target_grid": DEFAULT_PROFIT_TARGET_GRID,
            "candidates": candidates,
        }, indent=2))
        return

    results = run_variants(frames, ticks)

    output = {
        "tickers_used": sorted(frames.keys()),
        "lookback_days": LOOKBACK_DAYS,
        **{v: summarize(results[v], VARIANT_LABELS[v]) for v in STRATEGY_BUILDERS},
    }

    if split_window:
        first_half, second_half = split_ticks_by_midpoint(ticks)
        for half_name, half_ticks in (("first_half", first_half), ("second_half", second_half)):
            half_results = run_variants(frames, half_ticks)
            output[half_name] = {
                "n_ticks": len(half_ticks),
                **{v: summarize(half_results[v], VARIANT_LABELS[v]) for v in STRATEGY_BUILDERS},
            }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
