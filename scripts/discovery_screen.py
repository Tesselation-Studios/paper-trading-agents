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


def screen_tickers(tickers, min_price, max_price, return_all=False):
    """Fetch daily bars for tickers (one batched Alpaca call), filter to the
    price band, then to RSI 45-65 with real (or unknown -- fail open)
    volume, sorted by volume_ratio descending. Same candidate dict shape
    discovery_scan.screen_candidates() has always returned:
    {ticker, price, rsi, volume_ratio, macd_hist, in_band}.

    return_all=False (default, discovery_scan.py's use case): only in-band
    survivors, sorted -- unchanged behavior from before 'in_band' existed
    as a key (the extra key is harmless to every existing caller/test,
    none do exact-dict-equality checks).

    return_all=True (discovery_daemon.py's use case): every ticker that
    made it through the price-band filter, winners and losers alike, each
    tagged in_band True/False, unsorted -- this is what lets the
    continuous scanner's pool record 'this ticker was looked at and
    failed' as a real mechanized staleness signal, not just silence.
    Tickers excluded by the price band itself are not returned even here
    -- they're outside Stan's tradable range entirely, not worth pool
    tracking.
    """
    frames = replay_check.fetch_history(tickers)
    in_band_frames = universe_scan.filter_by_price_band(frames, min_price, max_price)

    results = []
    for ticker, df in in_band_frames.items():
        last = df.iloc[-1]
        rsi = float(last["rsi_14"])
        vol_ma20 = last.get("volume_ma20")
        volume = float(last["volume"])
        vol_ratio = volume / float(vol_ma20) if pd.notna(vol_ma20) and vol_ma20 else None
        passes_rsi = universe_scan.ENTRY_RSI_LOW < rsi < universe_scan.ENTRY_RSI_HIGH
        passes_volume = vol_ratio is None or vol_ratio >= MIN_VOLUME_RATIO
        results.append({
            "ticker": ticker, "price": float(last["close"]), "rsi": rsi,
            "volume_ratio": vol_ratio, "macd_hist": float(last["macd_hist"]),
            "in_band": passes_rsi and passes_volume,
        })

    if return_all:
        return results

    candidates = [c for c in results if c["in_band"]]
    candidates.sort(key=lambda c: c["volume_ratio"] if c["volume_ratio"] is not None else 0,
                     reverse=True)
    return candidates
