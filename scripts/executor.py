#!/usr/bin/env python3
"""Alpaca order executor for paper trading agents — with built-in trade guardrails.

Usage:
  python3 executor.py --account stonks --action status
  python3 executor.py --account stonks --action BUY --ticker SOFI --qty 2 --price 4.58 --conviction 0.6 --sector "Consumer Tech"
  python3 executor.py --account stonks --action SELL --ticker SOFI --qty 2 --price 4.58
  python3 executor.py --account stonks --action check-stops

BUY/SELL runs through a chain of guardrail gates before the order is placed —
position size, max positions, sector concentration, market hours, conviction
floor, cash. Each gate is a simple check(context, action) -> (bool, reason)
function; toggle any of them off in params.json's guardrail_gates block
without touching this file. check-stops scans open positions for hard-stop /
trailing-stop breaches (params.json risk.stop_loss_pct / risk.trailing_stop_pct).
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ALPACA_BASE_URL = "https://paper-api.alpaca.markets"

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
PARAMS_PATH = WORKSPACE_DIR / "params.json"
STATE_DIR = WORKSPACE_DIR / "state"
STOPS_STATE_PATH = STATE_DIR / "guardrail_stops.json"


# ─────────────────────────────────────────────────────────────────────────────
# Alpaca API
# ─────────────────────────────────────────────────────────────────────────────


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
    import urllib.request
    url = f"{ALPACA_BASE_URL}/v2/account"
    req = urllib.request.Request(url, headers=get_headers(account))
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_positions(account):
    import urllib.request
    url = f"{ALPACA_BASE_URL}/v2/positions"
    req = urllib.request.Request(url, headers=get_headers(account))
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def place_order(account, ticker, qty, side):
    import urllib.request
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


def load_params() -> Dict[str, Any]:
    with open(PARAMS_PATH) as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Guardrail gates — checked before a BUY/SELL is placed
#
# Each gate: check(context, action) -> (granted: bool, reason: str).
# Toggle any gate off via params.json guardrail_gates.<name> — no code change
# needed. Gates fail open on missing data or unexpected errors (never block a
# trade because of a data hiccup) but fail closed on an actual limit breach.
# ─────────────────────────────────────────────────────────────────────────────


def gate_cash(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    cost = float(action.get("quantity", 0)) * float(action.get("price", 0) or 0)
    if cost <= 0:
        return True, "no price data, skipped (fail-open)"
    cash = float(context.get("cash", 0))
    if cost > cash:
        return False, f"BUY costs ${cost:,.2f} but only ${cash:,.2f} cash available"
    return True, f"BUY costs ${cost:,.2f}, cash ${cash:,.2f} sufficient"


def gate_position_size(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    max_pct = float(load_params().get("risk", {}).get("max_position_pct", 6.0))
    ticker = str(action.get("ticker", "")).upper()
    price = float(action.get("price", 0) or 0)
    qty = float(action.get("quantity", 0))
    proposed_value = qty * price
    portfolio_value = float(context.get("portfolio_value", 0))
    if proposed_value <= 0 or portfolio_value <= 0:
        return True, "no price/portfolio data, skipped (fail-open)"

    existing_value = sum(
        float(p.get("market_value", 0))
        for p in context.get("positions", [])
        if str(p.get("symbol", "")).upper() == ticker
    )
    total_pct = (existing_value + proposed_value) / portfolio_value * 100

    if total_pct > max_pct:
        return False, (
            f"{ticker} would be {total_pct:.1f}% of portfolio "
            f"(existing ${existing_value:,.2f} + proposed ${proposed_value:,.2f}), exceeds {max_pct:.0f}% cap"
        )
    return True, f"{ticker} at {total_pct:.1f}% of portfolio, within {max_pct:.0f}% cap"


def gate_max_positions(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    max_positions = int(load_params().get("risk", {}).get("max_positions", 25))
    ticker = str(action.get("ticker", "")).upper()
    positions = context.get("positions", [])
    if any(str(p.get("symbol", "")).upper() == ticker for p in positions):
        return True, "adding to existing position, skipped"
    if len(positions) >= max_positions:
        return False, f"already at {len(positions)}/{max_positions} positions, no new tickers"
    return True, f"{len(positions)}/{max_positions} positions, room for new ticker"


def _sector_of(ticker: str) -> Optional[str]:
    thesis_path = WORKSPACE_DIR / "positions" / f"{ticker.upper()}.md"
    if not thesis_path.exists():
        return None
    try:
        for line in thesis_path.read_text().splitlines():
            if line.strip().lower().startswith("sector:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def gate_sector_concentration(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Sector lookup reads positions/<ticker>.md 'Sector:' line if present.
    No sector data available -> skip (fail-open), not a block."""
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    max_per_sector = int(load_params().get("risk_guards", {}).get("max_positions_per_sector", 2))
    ticker = str(action.get("ticker", "")).upper()
    sector = action.get("sector") or _sector_of(ticker)
    if not sector:
        return True, "no sector data, skipped (fail-open)"

    same_sector_count = sum(
        1 for p in context.get("positions", [])
        if str(p.get("symbol", "")).upper() != ticker and _sector_of(str(p.get("symbol", "")).upper()) == sector
    )
    if same_sector_count >= max_per_sector:
        return False, f"{sector} already has {same_sector_count} positions, at {max_per_sector} cap"
    return True, f"{sector} has {same_sector_count}/{max_per_sector} positions"


def gate_hours(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Reject any BUY/SELL outside 09:30-16:00 ET, Mon-Fri."""
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = datetime.datetime.now()

    if now.weekday() >= 5:
        return False, f"{now.strftime('%A')} — market closed on weekends"
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now < market_open or now > market_close:
        return False, f"{now.strftime('%H:%M %Z')} — market open 09:30-16:00 ET"
    return True, f"{now.strftime('%H:%M %Z')} — market open"


def gate_conviction(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    if action.get("conviction") is None:
        return True, "no conviction passed, skipped (fail-open)"
    floor = float(load_params().get("risk", {}).get("conviction_floor", 0.5))
    conviction = float(action["conviction"])
    if conviction < floor:
        return False, f"conviction {conviction:.2f} below {floor:.2f} floor"
    return True, f"conviction {conviction:.2f} >= {floor:.2f} floor"


# name -> gate function. params.json guardrail_gates.<name> = false disables it.
GATES = {
    "cash": gate_cash,
    "position_size": gate_position_size,
    "max_positions": gate_max_positions,
    "sector_concentration": gate_sector_concentration,
    "hours": gate_hours,
    "conviction": gate_conviction,
}


def check_order(account: str, action: str, ticker: str, qty: int, price: Optional[float] = None,
                 conviction: Optional[float] = None, sector: Optional[str] = None) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Run a proposed trade through all enabled gates. First rejection stops the chain."""
    account_data = get_account(account)
    positions = get_positions(account)
    context = {
        "portfolio_value": float(account_data.get("equity", 0)),
        "cash": float(account_data.get("cash", 0)),
        "positions": [{"symbol": p["symbol"], "market_value": float(p["market_value"])} for p in positions],
    }
    trade_action = {
        "action": action.upper(), "ticker": ticker.upper(), "quantity": qty,
        "price": price, "conviction": conviction, "sector": sector,
    }

    toggles = load_params().get("guardrail_gates", {})
    results = []
    for name, gate_fn in GATES.items():
        if not toggles.get(name, True):
            results.append({"gate": name, "passed": True, "reason": "disabled via params.json guardrail_gates"})
            continue
        try:
            passed, reason = gate_fn(context, trade_action)
        except Exception as e:
            passed, reason = True, f"ERROR (fail-open): {e}"
        results.append({"gate": name, "passed": passed, "reason": reason})
        if not passed:
            return False, f"Blocked by {name}: {reason}", results
    return True, "All gates passed", results


# ─────────────────────────────────────────────────────────────────────────────
# Stop-loss / trailing-stop scanning — run once per tick against open positions
# ─────────────────────────────────────────────────────────────────────────────


def _load_stop_state() -> Dict[str, Any]:
    if STOPS_STATE_PATH.exists():
        try:
            return json.loads(STOPS_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_stop_state(state: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    STOPS_STATE_PATH.write_text(json.dumps(state, indent=2))


def check_stops(account: str) -> List[Dict[str, Any]]:
    """Scan open positions for hard-stop or trailing-stop breaches.

    Hard stop: risk.stop_loss_pct below entry price (fixed floor).
    Trailing stop: risk.trailing_stop_pct below the highest price observed
    since entry (ratchets up only; persisted to state/guardrail_stops.json
    since this script runs fresh each tick, not a long-lived process).
    Either can be disabled via guardrail_gates.hard_stop / .trailing_stop.

    Returns breach dicts: {ticker, stop_type, reason, loss_pct}. Caller (tick
    loop) is responsible for actually executing the SELL.
    """
    params = load_params()
    toggles = params.get("guardrail_gates", {})
    hard_stop_pct = abs(float(params.get("risk", {}).get("stop_loss_pct", -10.0)))
    trailing_pct = float(params.get("risk", {}).get("trailing_stop_pct", 5.0))

    positions = get_positions(account)
    state = _load_stop_state()
    breaches = []
    held_tickers = set()

    for p in positions:
        ticker = p["symbol"].upper()
        held_tickers.add(ticker)
        entry_price = float(p["avg_entry_price"])
        current_price = float(p["current_price"])

        if toggles.get("hard_stop", True):
            hard_stop_price = entry_price * (1 - hard_stop_pct / 100)
            if current_price <= hard_stop_price:
                loss_pct = (current_price - entry_price) / entry_price * 100
                breaches.append({
                    "ticker": ticker, "stop_type": "hard",
                    "reason": f"{ticker}: {loss_pct:.1f}% loss >= {hard_stop_pct:.0f}% hard stop "
                              f"(${entry_price:.2f} -> ${current_price:.2f})",
                    "loss_pct": loss_pct,
                })
                continue  # already breached, don't also report trailing

        # Track peak regardless of whether trailing-stop gate is enabled, so
        # re-enabling it later doesn't start from a stale/reset peak.
        entry = state.get(ticker, {"peak_price": entry_price})
        peak_price = max(float(entry.get("peak_price", entry_price)), current_price)
        state[ticker] = {"peak_price": peak_price, "entry_price": entry_price}

        if toggles.get("trailing_stop", True):
            trail_stop_price = peak_price * (1 - trailing_pct / 100)
            if current_price <= trail_stop_price:
                drop_from_peak = (current_price - peak_price) / peak_price * 100
                breaches.append({
                    "ticker": ticker, "stop_type": "trailing",
                    "reason": f"{ticker}: trailing stop breached, {drop_from_peak:.1f}% off peak "
                              f"${peak_price:.2f} (stop ${trail_stop_price:.2f}, current ${current_price:.2f})",
                    "loss_pct": (current_price - entry_price) / entry_price * 100,
                })

    for ticker in list(state.keys()):
        if ticker not in held_tickers:
            del state[ticker]
    _save_stop_state(state)
    return breaches


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Alpaca order executor with built-in guardrails")
    parser.add_argument("--account", default="kairos", choices=["kairos", "aldridge", "stonks"])
    parser.add_argument("--action", choices=["BUY", "SELL", "status", "check-stops"])
    parser.add_argument("--ticker")
    parser.add_argument("--qty", type=int)
    parser.add_argument("--price", type=float, help="current/estimated price, used by guardrail checks")
    parser.add_argument("--conviction", type=float, help="0-1, used by the conviction gate on BUY")
    parser.add_argument("--sector", help="used by the sector-concentration gate")
    parser.add_argument("--skip-guardrails", action="store_true", help="bypass guardrail checks (debug only)")

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

    if args.action == "check-stops":
        breaches = check_stops(args.account)
        print(json.dumps({"breaches": breaches}, indent=2))
        return

    if not args.ticker or not args.qty:
        print(json.dumps({"error": "ticker and qty required for BUY/SELL"}))
        sys.exit(1)

    if not args.skip_guardrails:
        granted, reason, gate_results = check_order(
            args.account, args.action, args.ticker, args.qty,
            price=args.price, conviction=args.conviction, sector=args.sector,
        )
        if not granted:
            print(json.dumps({"error": f"guardrail: {reason}", "gates": gate_results}, indent=2))
            sys.exit(1)

    side = args.action.lower()
    order = place_order(args.account, args.ticker, args.qty, side)
    print(json.dumps(order, indent=2))


if __name__ == "__main__":
    main()
