#!/usr/bin/env python3
"""
Wide-universe hindsight backtest — answers two questions replay_check.py
can't: does the live strategy generalize beyond our current 9-11 held/
watchlisted tickers, and were there better plays in the same window that
we never even looked at?

Pulls a rotating random sample of tradable NYSE/NASDAQ tickers (default
~200 — Alpaca's paper account only gives ~200 days of daily bars or 3 days
of 5-min, so daily is the only workable granularity here, same as
replay_check.py), runs each one through the SAME strategy logic in
isolation (one ticker per backtest, not interleaved into one shared
portfolio), and ranks them by risk-adjusted return. Tickers already in
positions/watchlist are excluded from the ranking — they're not "missed,"
we already know about them.

For each top-ranked hindsight candidate, also reports the first date
within the window our actual entry signal (RSI 45-65) would have fired,
and how many days into the fetched window that was — a small number means
a broad daily scan running the whole time would have caught it almost
immediately; a large number means even a perfect scanner wouldn't have
had much runway before the move happened. Purely a synthetic computation
on the fetched bars, not a lookup of what Stan actually noticed at the
time.

Manual script — not wired into any cron yet (build trust in the output
first). Usage:
    python3 scripts/universe_scan.py [--sample-size 200] [--top 15] [--strategy v1.0] [--seed X]
"""
import argparse
import json
import os
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_check  # noqa: E402

sys.path.insert(0, "/home/openclaw/projects/paper-trading-rebuild")
from src.replay import replay_trader  # noqa: E402

from alpaca.trading.client import TradingClient  # noqa: E402
from alpaca.trading.requests import GetAssetsRequest  # noqa: E402
from alpaca.trading.enums import AssetClass, AssetExchange, AssetStatus  # noqa: E402

PLAIN_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
ENTRY_RSI_LOW, ENTRY_RSI_HIGH = 45, 65  # mirrors make_trader's v1.0/v1.1 entry band

# ── Common-stock filtering ───────────────────────────────────────────────
# PLAIN_TICKER_RE alone used to be the whole filter, with a docstring
# claiming it excluded preferred shares/warrants/class-shares. It doesn't:
# it only drops symbols with a dot or digit (WRB.PRF, IONQ.WS). Preferred
# tickers are just as plainly 1-5 uppercase letters -- LFMDP, VLYPN,
# BHFAL -- so they sailed straight through, and the discovery pool's
# promotion queue filled up with them and with thin ETFs.
#
# Alpaca has no security-type field to filter on (asset_class is
# US_EQUITY for common stock, ETFs, preferred shares and warrants alike),
# so the asset NAME is the only metadata that actually distinguishes
# them. That's what these match against.

# Not common stock at all: preferred/depositary shares, warrants, rights,
# units, and exchange-traded debt. Unambiguous from the name.
NON_COMMON_NAME_RE = re.compile(
    r"\b(preferred|depositary|depository|warrants?|rights?|units?|debenture|"
    r"notes? due|subordinated)\b",
    re.IGNORECASE,
)

# Funds rather than operating companies -- ETFs, ETNs, closed-end funds,
# index products. Deliberately does NOT include a bare "trust": that would
# take out REITs ("... Real Estate Investment Trust") and operating
# companies like Northern Trust. The cost is that a handful of legacy
# unit-trust ETFs whose names say only "Trust" (QQQ) still pass -- those
# are the liquid ones, not the illiquid junk this filter exists to stop.
FUND_NAME_RE = re.compile(
    r"\b(etf|etn|etv|exchange[- ]traded|funds?|index|portfolio|ucits)\b",
    re.IGNORECASE,
)

# Fallback only, for assets Alpaca returns without a name. NASDAQ's fifth
# letter encodes the security class; these are the codes for preferred
# (P), warrants (W), rights (R), units (U), bankruptcy (Q) and misc
# preferred/debenture (L/Z). Coarse -- it also catches genuine class
# shares like GOOGL -- but it only ever runs when the name metadata that
# would decide properly is missing.
CLASS_SUFFIX_TICKER_RE = re.compile(r"^[A-Z]{4}[PWRUQLZ]$")


def is_common_stock(symbol: str, name: str = None) -> bool:
    """Whether an Alpaca asset looks like tradable common stock, as opposed
    to a preferred share, warrant, unit, ETF or closed-end fund. Name-based
    when Alpaca gives a name (the accurate path), symbol-shape-based when
    it doesn't."""
    if not PLAIN_TICKER_RE.match(symbol):
        return False
    if not name:
        return not CLASS_SUFFIX_TICKER_RE.match(symbol)
    return not (NON_COMMON_NAME_RE.search(name) or FUND_NAME_RE.search(name))

# A ticker with 0-1 trades can show a wildly inflated Sharpe from a single
# lucky mark-to-market blip on an open position (e.g. one BUY held flat all
# window, no closed trades, technically "high Sharpe" on almost no signal).
# Require a minimum trade count before a ticker is eligible for the ranked
# list — thin-sample noise isn't a "smarter play," it's an artifact.
MIN_TRADES_FOR_RANKING = 3


def fetch_broad_universe(sample_size=200, seed=None):
    """Fetch every active/tradable NYSE+NASDAQ common-stock symbol (one
    cheap Alpaca call), drop preferred shares/warrants/units/ETFs via
    is_common_stock() on the asset's name metadata, then take a seeded
    random sample. Seeding by date
    (default) means a same-day rerun is reproducible but the sample
    rotates day to day — same spirit as the live discovery cron's
    "rotate session to session" rule.

    sample_size=None returns the full sorted candidate list, no sampling
    -- 2026-07-27, added for discovery_daemon.py's continuous scanner,
    which needs the whole tradable universe (to build its own persisted
    rotation cursor over it) rather than one day-seeded draw.
    """
    key = os.environ.get("ALPACA_STONKS_KEY")
    secret = os.environ.get("ALPACA_STONKS_SECRET")
    if not key or not secret:
        print(json.dumps({"error": "ALPACA_STONKS_KEY/SECRET not set"}))
        sys.exit(1)

    client = TradingClient(key, secret, paper=True)
    req = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
    assets = client.get_all_assets(req)

    candidates = sorted({
        a.symbol for a in assets
        if a.tradable
        and a.exchange in (AssetExchange.NASDAQ, AssetExchange.NYSE)
        and is_common_stock(a.symbol, getattr(a, "name", None))
    })

    if sample_size is None:
        return candidates

    rng = random.Random(seed if seed is not None else _today_seed())
    if len(candidates) <= sample_size:
        return candidates
    return sorted(rng.sample(candidates, sample_size))


def _today_seed():
    import datetime
    return datetime.date.today().isoformat()


def filter_by_price_band(frames, min_price, max_price):
    """Keep only tickers whose close price fell within [min_price,
    max_price] at some point in the fetched window — universe.min_price/
    max_price from params.json, same band the live discovery cron uses.
    """
    return {
        sym: df for sym, df in frames.items()
        if ((df["close"] >= min_price) & (df["close"] <= max_price)).any()
    }


def first_signal_date(df):
    """First chronological date the v1.0/v1.1 entry condition (RSI in the
    45-65 band) is true in this ticker's fetched history, or None if it
    never fires. Purely synthetic — the earliest a mechanical screen using
    today's entry rule could have flagged this ticker, not a record of
    when anyone actually noticed it.
    """
    in_band = df[(df["rsi_14"] > ENTRY_RSI_LOW) & (df["rsi_14"] < ENTRY_RSI_HIGH)]
    if in_band.empty:
        return None
    return in_band.iloc[0]["timestamp"]


def rank_tickers(frames, exclude, strategy_name="v1.0", top_n=15):
    """Run each ticker through an ISOLATED single-ticker backtest (its own
    tick stream, its own fresh portfolio — not interleaved with the other
    tickers) using the named strategy, then rank by Sharpe (falling back
    to total_return_pct when Sharpe can't be computed, e.g. too few
    trades). Tickers already known (current positions/watchlist) are
    excluded — the point is to surface what we didn't already have.
    """
    build_trader = replay_check.STRATEGY_BUILDERS[strategy_name]
    ranked = []
    for sym, df in frames.items():
        if sym in exclude:
            continue
        single = {sym: df}
        ticks = replay_check.build_tick_stream(single)
        if not ticks:
            continue
        trader_fn = build_trader(single)
        result = replay_trader(ticks, trader_fn, initial_balance=10_000.0,
                                max_position_pct=0.06, require_conviction=0.5)
        summary = replay_check.summarize(result, strategy_name)
        summary["ticker"] = sym

        signal_date = first_signal_date(df)
        summary["first_signal_date"] = signal_date.date().isoformat() if signal_date is not None else None
        if signal_date is not None:
            # How far into the fetched window the signal first fired — NOT
            # signal-to-entry lag (that's ~always 0 here: this isolated
            # backtest sees the ticker from day 1 and the trader acts on
            # the signal same-tick, so it can't measure real discovery
            # lag). A small number means a broad daily scan running the
            # whole window would have caught this almost immediately; a
            # large number means even a perfect scanner wouldn't have had
            # much runway before the move.
            summary["days_into_window"] = (signal_date - df["timestamp"].iloc[0]).days
        else:
            summary["days_into_window"] = None

        if summary["n_trades"] >= MIN_TRADES_FOR_RANKING:
            ranked.append(summary)

    def sort_key(s):
        if s["sharpe"] is not None:
            return (1, s["sharpe"])
        return (0, s["total_return_pct"])

    ranked.sort(key=sort_key, reverse=True)
    return ranked[:top_n]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=200)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--strategy", default="v1.0", choices=list(replay_check.STRATEGY_BUILDERS))
    parser.add_argument("--seed", default=None, help="Override the date-derived sample seed")
    args = parser.parse_args()

    with open(Path(__file__).resolve().parent.parent / "params.json") as f:
        params = json.load(f)
    min_price = params["universe"]["min_price"]
    max_price = params["universe"]["max_price"]

    sample = fetch_broad_universe(sample_size=args.sample_size, seed=args.seed)
    frames = replay_check.fetch_history(sample)
    in_band = filter_by_price_band(frames, min_price, max_price)

    already_known = set(replay_check.load_live_universe())
    top_candidates = rank_tickers(in_band, exclude=already_known,
                                   strategy_name=args.strategy, top_n=args.top)

    print(json.dumps({
        "sample_requested": args.sample_size,
        "sample_fetched_with_history": len(frames),
        "universe_after_price_filter": len(in_band),
        "excluded_as_already_known": sorted(already_known & in_band.keys()),
        "strategy": args.strategy,
        "top_hindsight_candidates": top_candidates,
    }, indent=2))


if __name__ == "__main__":
    main()
