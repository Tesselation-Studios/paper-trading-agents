#!/usr/bin/env python3
"""
discovery_scan.py — direct-Alpaca replacement for stonks-probe-discovery's
reliance on data-bus__get_technical_scan.

2026-07-23: the discovery cron failed 4 consecutive times (timeout at
"model-call-started" — the very first tool call never returned), and the
prior successful run already logged "Technical scan API returned
unavailable... News collector hung on network" — the same never-root-
caused data-bus MCP flakiness flagged earlier this session. This script
bypasses that dependency entirely: same direct-Alpaca pattern already
proven reliable all session (replay_check.py/universe_scan.py/
llm_replay.py never touch data-bus at all).

Screens a rotating sample of the live universe for the SAME entry signal
strategy.md already uses (RSI 45-65 + real volume), confirms survivors
with real Alpaca News (news_collector.py's fetch_alpaca_news + FinBERT,
built 2026-07-22 — proven reliable, unlike the data-bus sentiment path),
and writes results in the exact discoveries/YYYY-MM-DD.md format
merge_discoveries.py already consumes — no downstream changes needed.

Universe breadth is bankroll-scaled (bankroll.universe_max_price_for_ceiling)
— mechanizes strategy.md's Growth Trajectory section instead of leaving
it as unenforced prose.

Usage:
    python3 scripts/discovery_scan.py [--sample-size N] [--top N]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_check  # noqa: E402
import universe_scan  # noqa: E402
import news_collector  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DISCOVERIES_DIR = REPO_ROOT / "discoveries"
PARAMS_PATH = REPO_ROOT / "params.json"

DEFAULT_SAMPLE_SIZE = 60
DEFAULT_TOP_N = 6
MIN_VOLUME_RATIO = 0.8  # below-average volume isn't "real volume" per strategy.md


def get_universe_price_band():
    """params.json's min_price + bankroll-scaled max_price — real, not
    the dead experience.json.peak_ceiling table TOOLS.md used to point at.
    """
    sys.path.insert(0, str(REPO_ROOT))
    import bankroll  # noqa: E402

    params = json.loads(PARAMS_PATH.read_text())
    min_price = params.get("universe", {}).get("min_price", 1.0)
    state = bankroll.read_bankroll()
    max_price = bankroll.universe_max_price_for_ceiling(state["ceiling"])
    return min_price, max_price


def screen_candidates(sample_size=DEFAULT_SAMPLE_SIZE, seed=None):
    """Returns candidates currently in the RSI 45-65 entry band with real
    (not below-average) volume — same signal strategy.md's entry rule
    already uses, computed directly from Alpaca, no data-bus dependency.
    """
    min_price, max_price = get_universe_price_band()
    sample = universe_scan.fetch_broad_universe(sample_size=sample_size, seed=seed)
    frames = replay_check.fetch_history(sample)
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


def confirm_with_news(candidates, top_n=DEFAULT_TOP_N):
    """Real Alpaca News + FinBERT confirmation on the top screened
    candidates — research/news signal, not just technicals. A candidate
    with no recent news isn't rejected (small-caps often have none), just
    reported honestly as such, same "don't fabricate" rule as everywhere
    else in this repo."""
    top = candidates[:top_n]
    for c in top:
        articles = news_collector.fetch_alpaca_news([c["ticker"]], limit=5)
        if articles:
            combined = " ".join(f"{a['title']} {a.get('summary', '')}" for a in articles)
            c["sentiment"] = news_collector.score_sentiment(combined)
            c["news_headline"] = articles[0]["title"]
        else:
            c["sentiment"] = None
            c["news_headline"] = None
    return top


def write_discoveries_file(candidates, min_price, max_price, path=None):
    today = datetime.now(timezone.utc).date().isoformat()
    path = path or (DISCOVERIES_DIR / f"{today}.md")
    DISCOVERIES_DIR.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# Probe Discovery — {today}",
        "",
        f"Direct-Alpaca scan (scripts/discovery_scan.py) — bankroll-scaled universe "
        f"${min_price:.0f}-${max_price:.0f}, RSI 45-65 + real volume, real news confirmation.",
        "",
        "---",
        "",
    ]
    for c in candidates:
        lines.append(f"## {c['ticker']} — ${c['price']:.2f}")
        lines.append(f"- RSI(14): {c['rsi']:.1f}"
                      + (f", volume {c['volume_ratio']:.2f}x 20d avg" if c["volume_ratio"] else ""))
        if c.get("news_headline"):
            sentiment_str = f"{c['sentiment']:+.2f}" if c["sentiment"] is not None else "n/a"
            lines.append(f"- News: \"{c['news_headline']}\" (sentiment {sentiment_str})")
        else:
            lines.append("- News: none found — technical signal only")
        lines.append("")

    path.write_text("\n".join(lines))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--seed", default=None)
    args = parser.parse_args()

    min_price, max_price = get_universe_price_band()
    candidates = screen_candidates(sample_size=args.sample_size, seed=args.seed)
    top = confirm_with_news(candidates, top_n=args.top)
    path = write_discoveries_file(top, min_price, max_price)

    print(json.dumps({
        "universe_price_band": [min_price, max_price],
        "sample_size": args.sample_size,
        "candidates_screened": len(candidates),
        "top_written": [c["ticker"] for c in top],
        "discoveries_file": str(path),
    }, indent=2))


if __name__ == "__main__":
    main()
