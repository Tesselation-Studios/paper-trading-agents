#!/usr/bin/env python3
"""
discovery_screen.py — pure ticker-list-in / candidates-out screening logic,
extracted from discovery_scan.py's screen_candidates() on 2026-07-27 so the
continuous discovery_daemon.py can share the exact same RSI/volume filter
without duplicating it. discovery_scan.screen_candidates() is now a thin
wrapper around screen_tickers() below (sample selection + this call).

No universe sourcing or price-band lookup here on purpose — those stay in
discovery_scan.py (get_universe_price_band(), universe_scan.fetch_broad_universe())
so this module has one job: given tickers + a price band, screen them.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_check  # noqa: E402
import universe_scan  # noqa: E402

MIN_VOLUME_RATIO = 0.8  # below-average volume isn't "real volume" per strategy.md


def screen_tickers(tickers, min_price, max_price):
    """Fetch daily bars for tickers (one batched Alpaca call), filter to the
    price band, then to RSI 45-65 with real (or unknown -- fail open)
    volume, sorted by volume_ratio descending. Same candidate dict shape
    discovery_scan.screen_candidates() has always returned:
    {ticker, price, rsi, volume_ratio, macd_hist}.
    """
    frames = replay_check.fetch_history(tickers)
    in_band_frames = universe_scan.filter_by_price_band(frames, min_price, max_price)

    candidates = []
    for ticker, df in in_band_frames.items():
        last = df.iloc[-1]
        rsi = float(last["rsi_14"])
        if not (universe_scan.ENTRY_RSI_LOW < rsi < universe_scan.ENTRY_RSI_HIGH):
            continue
        vol_ma20 = last.get("volume_ma20")
        volume = float(last["volume"])
        vol_ratio = volume / float(vol_ma20) if pd.notna(vol_ma20) and vol_ma20 else None
        if vol_ratio is not None and vol_ratio < MIN_VOLUME_RATIO:
            continue
        candidates.append({
            "ticker": ticker, "price": float(last["close"]), "rsi": rsi,
            "volume_ratio": vol_ratio, "macd_hist": float(last["macd_hist"]),
        })

    candidates.sort(key=lambda c: c["volume_ratio"] if c["volume_ratio"] is not None else 0,
                     reverse=True)
    return candidates
