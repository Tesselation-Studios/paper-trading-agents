#!/usr/bin/env python3
"""
Off-hours replay check — the actual "would the new hardened rule have
helped" test that was missing from the nightly evolve step. Runs the SAME
simple entry strategy through the historical replay harness twice: once
with the OLD exit rules (fixed stop-loss/profit-target only) and once with
the NEW v1.1 rules (+ MACD-histogram-flip immediate exit, + regime-gated
entries) — isolating exactly what changed, instead of trusting the
promotion on narrative confidence alone.

Uses paper-trading-rebuild's src/replay.py ReplayHarness (imported
directly — no repo-specific deps in that module). Historical daily bars
come straight from Alpaca (market_data.bars_1d only has real history for
NVDA among Stonks's holdings — everything else is empty there), same
approach as the Trading Terminal's indicators.py.

Usage:
    python3 scripts/replay_check.py
"""
import json
import os
import sys

import pandas as pd
import pandas_ta as ta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

sys.path.insert(0, "/home/openclaw/projects/paper-trading-rebuild")
from src.replay import Tick, TraderDecision, replay_trader  # noqa: E402

LOOKBACK_DAYS = 200
RSI_LENGTH = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
CHOPPY_VOL_THRESHOLD = 0.035  # 20d rolling stdev of daily returns

# Mirrors params.json's current values.
STOP_LOSS_PCT = -10.0
PROFIT_TARGET_PCT = 12.0


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
        df["macd_hist"] = macd_df.iloc[:, 2]
        df["daily_return"] = df["close"].pct_change()
        df["vol_20d"] = df["daily_return"].rolling(20).std()
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


def make_trader(frames, use_v11_rules):
    """Build a trader function closing over per-ticker MACD-hist/vol lookups
    (Tick has no macd field) and a small amount of flip-detection state."""
    macd_lookup = {}
    vol_lookup = {}
    for sym, df in frames.items():
        for _, row in df.iterrows():
            key = (sym, row["timestamp"])
            macd_lookup[key] = float(row["macd_hist"])
            vol_lookup[key] = float(row["vol_20d"])

    prev_macd = {}

    def trader(tick, portfolio):
        key = (tick.ticker, tick.timestamp)
        macd_hist = macd_lookup.get(key)
        vol_20d = vol_lookup.get(key)
        held = portfolio.positions.get(tick.ticker)

        if held is not None:
            pnl_pct = (tick.close - held.entry_price) / held.entry_price * 100

            if use_v11_rules and macd_hist is not None:
                last = prev_macd.get(tick.ticker)
                prev_macd[tick.ticker] = macd_hist
                if last is not None and last > 0 and macd_hist < 0:
                    return TraderDecision(ticker=tick.ticker, decision="SELL",
                                           conviction=1.0, rationale="v1.1: MACDh flip positive->negative")

            if pnl_pct <= STOP_LOSS_PCT:
                return TraderDecision(ticker=tick.ticker, decision="SELL",
                                       conviction=1.0, rationale="stop_loss_pct breached")
            if pnl_pct >= PROFIT_TARGET_PCT:
                return TraderDecision(ticker=tick.ticker, decision="SELL",
                                       conviction=1.0, rationale="profit_target_pct reached")
            return TraderDecision(ticker=tick.ticker, decision="HOLD", conviction=0.0)

        if macd_hist is not None:
            last = prev_macd.get(tick.ticker)
            prev_macd[tick.ticker] = macd_hist

        # Same simple momentum entry in BOTH variants — the only thing under
        # test is the exit/entry-gating logic that changed in v1.1.
        if tick.rsi is not None and 45 < tick.rsi < 65:
            if use_v11_rules and vol_20d is not None and vol_20d > CHOPPY_VOL_THRESHOLD:
                return TraderDecision(ticker=tick.ticker, decision="HOLD", conviction=0.0,
                                       rationale="v1.1: regime-gated, choppy/high-vol")
            return TraderDecision(ticker=tick.ticker, decision="BUY", conviction=0.6,
                                   rationale="momentum entry: RSI 45-65")
        return TraderDecision(ticker=tick.ticker, decision="HOLD", conviction=0.0)

    return trader


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
    }


def main():
    tickers = sys.argv[1:] if len(sys.argv) > 1 else [
        "CHWY", "F", "FUBO", "GME", "KHC", "LYFT", "MVST", "NVDA", "SOFI",
    ]

    frames = fetch_history(tickers)
    if not frames:
        print(json.dumps({"error": "no usable history for any ticker"}))
        return

    ticks = build_tick_stream(frames)

    old_trader = make_trader(frames, use_v11_rules=False)
    new_trader = make_trader(frames, use_v11_rules=True)

    old_result = replay_trader(ticks, old_trader, initial_balance=10_000.0,
                                max_position_pct=0.06, require_conviction=0.5)
    new_result = replay_trader(ticks, new_trader, initial_balance=10_000.0,
                                max_position_pct=0.06, require_conviction=0.5)

    print(json.dumps({
        "tickers_used": sorted(frames.keys()),
        "lookback_days": LOOKBACK_DAYS,
        "old_rules_v1.0": summarize(old_result, "pre-v1.1 (fixed stop/target only)"),
        "new_rules_v1.1": summarize(new_result, "v1.1 (+ MACDh-flip exit, + regime-gated entries)"),
    }, indent=2))


if __name__ == "__main__":
    main()
