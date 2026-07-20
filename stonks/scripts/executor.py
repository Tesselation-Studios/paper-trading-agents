#!/usr/bin/env python3
"""Alpaca order executor for paper trading agents.

Usage:
  python3 executor.py --account kairos --action BUY --ticker SOFI --qty 2
  python3 executor.py --account kairos --action SELL --ticker SOFI --qty 2
  python3 executor.py --account kairos --action status
"""

import argparse
import json
import os
import sys
import urllib.request
from urllib.error import URLError

ALPACA_BASE_URL = "https://paper-api.alpaca.markets"


def _get_keys(account):
    """Fetch API keys from environment variables."""
    key = os.environ.get(f"ALPACA_{account.upper()}_KEY")
    secret = os.environ.get(f"ALPACA_{account.upper()}_SECRET")
    if not key or not secret:
        raise RuntimeError(f"Missing Alpaca API keys for account '{account}'. Set ALPACA_{account.upper()}_KEY / ALPACA_{account.upper()}_SECRET.")
    return {"key": key, "secret": secret}


def get_headers(account):
    keys = _get_keys(account)
    return {
        "APCA-API-KEY-ID": keys["key"],
        "APCA-API-SECRET-KEY": keys["secret"],
        "Content-Type": "application/json",
    }


def get_account(account):
    url = f"{ALPACA_BASE_URL}/v2/account"
    req = urllib.request.Request(url, headers=get_headers(account))
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_positions(account):
    url = f"{ALPACA_BASE_URL}/v2/positions"
    req = urllib.request.Request(url, headers=get_headers(account))
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def place_order(account, ticker, qty, side):
    data = {
        "symbol": ticker,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    url = f"{ALPACA_BASE_URL}/v2/orders"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers=get_headers(account),
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main():
    parser = argparse.ArgumentParser(description="Alpaca order executor")
    parser.add_argument("--account", default="kairos", choices=["kairos", "aldridge", "stonks"])
    parser.add_argument("--action", choices=["BUY", "SELL", "status"])
    parser.add_argument("--ticker")
    parser.add_argument("--qty", type=int)

    args = parser.parse_args()

    if args.action == "status":
        account_data = get_account(args.account)
        positions = get_positions(args.account)
        result = {
            "portfolio_value": float(account_data.get("equity", 0)),
            "cash": float(account_data.get("cash", 0)),
            "positions": [
                {
                    "symbol": p["symbol"],
                    "qty": int(float(p["qty"])),
                    "market_value": float(p["market_value"]),
                    "unrealized_pl": float(p["unrealized_pl"]),
                    "unrealized_plpc": float(p["unrealized_plpc"]),
                    "avg_entry_price": float(p["avg_entry_price"]),
                    "current_price": float(p["current_price"]),
                }
                for p in positions
            ],
            "buying_power": float(account_data.get("buying_power", 0)),
            "daytrade_count": account_data.get("daytrade_count", 0),
        }
        print(json.dumps(result, indent=2))
        return

    if not args.ticker or not args.qty:
        print(json.dumps({"error": "ticker and qty required for BUY/SELL"}))
        sys.exit(1)

    side = args.action.lower()
    order = place_order(args.account, args.ticker, args.qty, side)
    print(json.dumps(order, indent=2))


if __name__ == "__main__":
    main()
