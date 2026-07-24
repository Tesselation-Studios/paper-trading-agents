#!/usr/bin/env python3
"""append_discovery.py — log a freeform-research candidate into today's
discoveries/YYYY-MM-DD.md, in the exact format discovery_scan.py already
writes and merge_discoveries.py already consumes. Companion to the
deterministic RSI/volume screen, not a replacement — see
skills/freeform-discovery.md.

Usage:
    python3 scripts/append_discovery.py --ticker XYZ --price 12.34 \
        --note "Reuters: XYZ wins DoD contract, 3x avg pre-market volume"
    python3 scripts/append_discovery.py --ticker XYZ --price 12.34 \
        --note "..." --source freeform
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discovery_scan  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--price", type=float, required=True)
    parser.add_argument("--note", required=True, help="Why this is a real candidate — the catalyst, not a vague hunch")
    parser.add_argument("--source", default="freeform")
    args = parser.parse_args()

    min_price, max_price = discovery_scan.get_universe_price_band()
    ticker = args.ticker.strip().upper()

    if not (min_price <= args.price <= max_price):
        print(json.dumps({
            "error": f"price ${args.price:.2f} outside today's bankroll-scaled universe "
                     f"(${min_price:.2f}-${max_price:.2f})",
            "written": None,
        }, indent=2))
        return 1

    candidate = {"ticker": ticker, "price": args.price, "note": args.note, "source": args.source}
    path = discovery_scan.write_discoveries_file([candidate], min_price, max_price)

    print(json.dumps({
        "written": ticker,
        "discoveries_file": str(path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
