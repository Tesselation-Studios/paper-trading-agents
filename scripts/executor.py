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
import contextlib
import fcntl
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import db_writer
import trader_db

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
PARAMS_PATH = WORKSPACE_DIR / "params.json"
STATE_DIR = WORKSPACE_DIR / "state"
STOPS_STATE_PATH = STATE_DIR / "guardrail_stops.json"
RECENT_ORDERS_PATH = STATE_DIR / "recent_orders.json"
DAILY_ORDER_COUNT_PATH = STATE_DIR / "daily_order_count.json"
PEAK_EQUITY_PATH = STATE_DIR / "peak_equity.json"
EXPERIENCE_PATH = WORKSPACE_DIR / "experience.json"

_DEFAULT_ALPACA_BASE_URL = "https://paper-api.alpaca.markets"


def _get_alpaca_base_url() -> str:
    """Reads params.json.alpaca.base_url fresh, falling back to the paper
    trading default if params.json is missing/malformed — 2026-07-23:
    this used to be a hardcoded constant while params.json.alpaca.base_url
    sat there unread (found by workspace_review.py's dead-param check),
    silently ignored no matter what it said. Now it's actually live."""
    try:
        with open(PARAMS_PATH, 'r') as f:
            params = json.load(f)
        return params.get("alpaca", {}).get("base_url", _DEFAULT_ALPACA_BASE_URL)
    except (OSError, json.JSONDecodeError):
        return _DEFAULT_ALPACA_BASE_URL


ALPACA_BASE_URL = _get_alpaca_base_url()


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


def _record_alpaca_call(endpoint: str, method: str, request_summary: Optional[dict],
                         status_code: Optional[int], start_time: float) -> None:
    """Best-effort Alpaca API audit log (2026-07-28) -- never raises, never
    blocks or slows the real trading call it's instrumenting. request_summary
    is a small dict (ticker/qty/side), never headers/keys."""
    try:
        db_writer.enqueue("alpaca_audit_log", {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint,
            "method": method,
            "request_summary": json.dumps(request_summary) if request_summary else None,
            "status_code": status_code,
            "response_summary": None,
            "latency_ms": int((time.time() - start_time) * 1000),
        })
        db_writer.flush_all()
    except Exception:
        pass


def get_account(account):
    import urllib.request
    url = f"{ALPACA_BASE_URL}/v2/account"
    req = urllib.request.Request(url, headers=get_headers(account))
    start = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        _record_alpaca_call("account", "GET", None, 200, start)
        return body
    except Exception as e:
        _record_alpaca_call("account", "GET", None, getattr(e, "code", None), start)
        raise


def get_positions(account):
    import urllib.request
    url = f"{ALPACA_BASE_URL}/v2/positions"
    req = urllib.request.Request(url, headers=get_headers(account))
    start = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        _record_alpaca_call("positions", "GET", None, 200, start)
        return body
    except Exception as e:
        _record_alpaca_call("positions", "GET", None, getattr(e, "code", None), start)
        raise


def get_open_orders(account, ticker=None):
    """GET /v2/orders?status=open[&symbols=<ticker>] — Alpaca's own live
    order book. Used by gate_order_idempotency (2026-07-27) as the
    authoritative, cross-process duplicate-BUY guard: unlike
    state/recent_orders.json (this process's own memory), this reflects
    reality regardless of which process/session is asking."""
    import urllib.parse
    import urllib.request
    url = f"{ALPACA_BASE_URL}/v2/orders?status=open"
    if ticker:
        url += f"&symbols={urllib.parse.quote(str(ticker).upper())}"
    req = urllib.request.Request(url, headers=get_headers(account))
    start = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        _record_alpaca_call("orders", "GET", {"ticker": ticker} if ticker else None, 200, start)
        return body
    except Exception as e:
        _record_alpaca_call("orders", "GET", {"ticker": ticker} if ticker else None, getattr(e, "code", None), start)
        raise


def place_order(account, ticker, qty, side):
    import urllib.request
    # params.json.alpaca.order_type/time_in_force used to be dead config —
    # this hardcoded its own values regardless of what params.json said
    # (found by workspace_review.py's dead-param check, 2026-07-23). Read
    # fresh, fall back to the prior hardcoded defaults on any read error.
    try:
        alpaca_params = load_params().get("alpaca", {})
    except (OSError, ValueError):
        alpaca_params = {}
    data = {
        "symbol": ticker,
        "qty": str(qty),
        "side": side,
        "type": alpaca_params.get("order_type", "market"),
        "time_in_force": alpaca_params.get("time_in_force", "day"),
    }
    url = f"{ALPACA_BASE_URL}/v2/orders"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers=get_headers(account),
        method="POST",
    )
    request_summary = {"ticker": ticker, "qty": qty, "side": side}
    start = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        _record_alpaca_call("orders", "POST", request_summary, 200, start)
        return body
    except Exception as e:
        _record_alpaca_call("orders", "POST", request_summary, getattr(e, "code", None), start)
        raise


def get_order(account, order_id):
    """GET /v2/orders/<id> — used to confirm a BUY actually filled before a
    protective stop is sized against it."""
    import urllib.parse
    import urllib.request
    url = f"{ALPACA_BASE_URL}/v2/orders/{urllib.parse.quote(str(order_id))}"
    req = urllib.request.Request(url, headers=get_headers(account))
    start = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        _record_alpaca_call("orders", "GET", {"order_id": str(order_id)}, 200, start)
        return body
    except Exception as e:
        _record_alpaca_call("orders", "GET", {"order_id": str(order_id)}, getattr(e, "code", None), start)
        raise


def cancel_order(account, order_id):
    """DELETE /v2/orders/<id>. Alpaca answers 204 (accepted) or 422 if the
    order is already in a non-cancelable state — the caller treats both as
    "it's not going to sit on the book", see cancel_protective_stops."""
    import urllib.parse
    import urllib.request
    url = f"{ALPACA_BASE_URL}/v2/orders/{urllib.parse.quote(str(order_id))}"
    req = urllib.request.Request(url, headers=get_headers(account), method="DELETE")
    start = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            status = getattr(resp, "status", 204)
        _record_alpaca_call("orders", "DELETE", {"order_id": str(order_id)}, status, start)
        return True
    except Exception as e:
        _record_alpaca_call("orders", "DELETE", {"order_id": str(order_id)}, getattr(e, "code", None), start)
        raise


def get_closed_orders(account, ticker, limit: int = 10):
    """GET /v2/orders?status=closed&symbols=<ticker> — most recent first.
    Used to find the fill behind a position that disappeared from Alpaca
    without this file having sold it (a broker-side protective stop that
    triggered while no tick was running)."""
    import urllib.parse
    import urllib.request
    query = urllib.parse.urlencode({
        "status": "closed",
        "symbols": str(ticker).upper(),
        "limit": limit,
        "direction": "desc",
    })
    url = f"{ALPACA_BASE_URL}/v2/orders?{query}"
    req = urllib.request.Request(url, headers=get_headers(account))
    start = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        _record_alpaca_call("orders", "GET", {"ticker": str(ticker).upper(), "status": "closed"}, 200, start)
        return body
    except Exception as e:
        _record_alpaca_call("orders", "GET", {"ticker": str(ticker).upper(), "status": "closed"},
                             getattr(e, "code", None), start)
        raise


def place_stop_order(account, ticker, qty, stop_price):
    """Submit a broker-side protective stop (sell stop, GTC) at Alpaca.

    2026-08-01: check_stops() only ever DETECTED breaches and handed a list
    back for the agent to act on in the same tick — nothing existed at the
    broker. ~23% of ticks error out (mostly cron timeouts), and during any
    tick outage or gateway restart no stop check ran and no exit fired, for
    however long the outage lasted. strategy.md claimed stops were
    "mechanically enforced in executor.py's guardrail gates"; this is what
    finally makes the hard floor true — a resting order that survives a
    dead tick, a crashed gateway, and an overnight gap.

    time_in_force is deliberately hardcoded 'gtc' rather than read from
    params.json.alpaca.time_in_force ('day'): a day-scoped protective stop
    would silently evaporate at the close, which is precisely the outage
    window it exists to cover. type/order_type params.json values likewise
    don't apply — this is a stop order by definition.
    """
    import urllib.request
    data = {
        "symbol": str(ticker).upper(),
        "qty": str(int(qty)),
        "side": "sell",
        "type": "stop",
        "time_in_force": "gtc",
        "stop_price": f"{stop_price:.4f}".rstrip("0").rstrip("."),
    }
    url = f"{ALPACA_BASE_URL}/v2/orders"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers=get_headers(account),
        method="POST",
    )
    request_summary = {"ticker": str(ticker).upper(), "qty": int(qty), "side": "sell",
                        "type": "stop", "stop_price": stop_price}
    start = time.time()
    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
        _record_alpaca_call("orders", "POST", request_summary, 200, start)
        return body
    except Exception as e:
        _record_alpaca_call("orders", "POST", request_summary, getattr(e, "code", None), start)
        raise


def load_params() -> Dict[str, Any]:
    with open(PARAMS_PATH) as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Broker-side protective stops
#
# The static worst-case backstop that survives a tick timeout or a gateway
# restart. check_stops()'s ratcheting trailing stop still runs on top of
# this and is normally tighter/dynamic — this is the floor underneath it,
# not a replacement for it.
#
# Everything here is best-effort: a protective-stop failure must never look
# like a failed trade (the real order has already executed by the time any
# of it runs), same fail-open philosophy as close_trade_outcome().
# ─────────────────────────────────────────────────────────────────────────────

PROTECTIVE_STOP_ORDER_TYPES = ("stop", "stop_limit", "trailing_stop")


def _order_type_of(order: Dict[str, Any]) -> str:
    return str(order.get("type") or order.get("order_type") or "").lower()


def is_protective_stop_order(order: Dict[str, Any]) -> bool:
    """A resting sell stop this file placed (or would replace). Deliberately
    matches on side+type rather than an order tag: an Alpaca-side manual
    order or one left over from a previous run has to be reconciled too,
    and stop/stop_limit/trailing_stop sells are all the same thing for the
    purpose of 'don't leave two conflicting exits on one position'."""
    if not isinstance(order, dict):
        return False
    return (str(order.get("side", "")).lower() == "sell"
            and _order_type_of(order) in PROTECTIVE_STOP_ORDER_TYPES)


def _round_stop_price(price: float) -> float:
    """Alpaca accepts sub-penny increments only below $1.00 (4 decimals);
    at or above $1.00 a stop price must be in penny increments or the order
    is rejected. Rounds DOWN so rounding can never tighten a stop into a
    price it wasn't meant to trigger at."""
    if price >= 1.0:
        return math.floor(price * 100) / 100
    return math.floor(price * 10000) / 10000


def protective_stop_price(entry_price: float, params: Optional[Dict[str, Any]] = None) -> float:
    """entry_price * (1 - risk.stop_loss_pct/100) — the same hard floor
    check_stops() compares against, so the resting order and the in-tick
    scan can't disagree about where the stop is."""
    params = params if params is not None else load_params()
    stop_loss_pct = abs(float(params.get("risk", {}).get("stop_loss_pct", -10.0)))
    return _round_stop_price(entry_price * (1 - stop_loss_pct / 100.0))


def _protective_stops_enabled(params: Optional[Dict[str, Any]] = None) -> bool:
    """params.json guardrail_gates.broker_stop_order, default on — same
    convention as every other gate toggle (missing == enabled)."""
    try:
        params = params if params is not None else load_params()
        return params.get("guardrail_gates", {}).get("broker_stop_order", True) is not False
    except (OSError, ValueError):
        return True


def open_protective_stops(account: str, ticker: Optional[str] = None) -> List[Dict[str, Any]]:
    """Resting sell-stop orders at Alpaca, optionally for one ticker.
    Returns [] on any API error (never raises) — callers treat "can't tell"
    as "nothing to clean up" and move on."""
    try:
        orders = get_open_orders(account, ticker)
    except Exception:
        return []
    if not isinstance(orders, list):
        return []
    return [o for o in orders if is_protective_stop_order(o)]


def cancel_protective_stops(account: str, ticker: str, wait: bool = True,
                             orders: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Cancel every resting protective stop on `ticker`. Returns the ids
    actually cancelled.

    This is not optional cleanup — it's a correctness requirement on the
    SELL path. A GTC sell stop reserves the shares it covers, so a market
    SELL of the same position is rejected by Alpaca for insufficient
    quantity while that stop is still on the book. Cancel first, then sell.
    It's also what stops a stale stop (wrong size after a trim, wrong price
    after a re-entry) from sitting behind a position it no longer fits.
    """
    cancelled = []
    if orders is None:
        orders = open_protective_stops(account, ticker)
    for order in orders:
        order_id = order.get("id")
        if not order_id:
            continue
        try:
            cancel_order(account, order_id)
            cancelled.append(str(order_id))
        except Exception:
            # 422 = already filled/cancelled/expired; anything else is an API
            # hiccup. Either way it's not ours to retry here — the SELL below
            # will fail loudly on its own if the order really is still holding
            # the shares, which is the visible failure we want.
            continue
    if cancelled and wait:
        _wait_orders_cleared(account, ticker, cancelled)
    return cancelled


def _wait_orders_cleared(account: str, ticker: str, order_ids: List[str],
                          timeout: float = 3.0, interval: float = 0.25) -> bool:
    """Cancellation at Alpaca is accepted asynchronously — poll until the
    cancelled ids are off the open book (or timeout) so the SELL that
    follows isn't racing shares that are still reserved."""
    pending = {str(i) for i in order_ids}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            still_open = {str(o.get("id")) for o in get_open_orders(account, ticker)}
        except Exception:
            return False
        if not (pending & still_open):
            return True
        time.sleep(interval)
    return False


def wait_for_fill(account: str, order_id: str, timeout: float = 1.0,
                   interval: float = 0.5) -> Optional[Dict[str, Any]]:
    """Poll an order briefly until it reports filled. Returns the last seen
    order dict, or None if it can't be read at all.

    Deliberately short: this is only used to read back the true fill price
    for the training-example row. Protective-stop sizing does NOT depend on
    it — ensure_protective_stop() sizes against the position Alpaca actually
    reports, which is the only number that can't be wrong. A market BUY in
    paper trading fills in well under a second; anything slower is picked up
    by the next check-stops reconcile rather than blocking the tick here."""
    deadline = time.time() + timeout
    order = None
    while True:
        try:
            order = get_order(account, order_id)
        except Exception:
            return order
        if not isinstance(order, dict):
            return None
        if str(order.get("status", "")).lower() in ("filled", "partially_filled"):
            return order
        if time.time() >= deadline:
            return order
        time.sleep(interval)


def _held_position(account: str, ticker: str) -> Optional[Dict[str, Any]]:
    try:
        for p in get_positions(account):
            if str(p.get("symbol", "")).upper() == str(ticker).upper():
                return p
    except Exception:
        return None
    return None


def ensure_protective_stop(account: str, ticker: str, position: Optional[Dict[str, Any]] = None,
                            params: Optional[Dict[str, Any]] = None,
                            existing_orders: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Make Alpaca's resting protective stop for `ticker` match the position
    actually held: right size, right price, exactly one of them.

    Idempotent by design — if the existing stop already covers the held
    quantity at the correct price it is left alone rather than
    cancelled/replaced, so calling this every tick costs no order churn.
    `position`/`existing_orders` let a caller looping over the whole book
    (reconcile_protective_stops) pass data it has already fetched instead
    of re-querying Alpaca once per ticker.

    Never raises. Returns a small status dict for the CLI output.
    """
    ticker = str(ticker).upper()
    if not _protective_stops_enabled(params):
        return {"ticker": ticker, "status": "disabled"}

    try:
        position = position if position is not None else _held_position(account, ticker)
        if not position:
            cancelled = cancel_protective_stops(account, ticker, wait=False, orders=existing_orders)
            return {"ticker": ticker, "status": "no_position", "cancelled": cancelled}

        qty = int(float(position.get("qty", 0)))
        entry_price = float(position.get("avg_entry_price", 0) or 0)
        current_price = float(position.get("current_price", 0) or 0)
        if qty < 1 or entry_price <= 0:
            return {"ticker": ticker, "status": "skipped", "reason": "no whole-share qty / entry price"}

        stop_price = protective_stop_price(entry_price, params)
        existing = (existing_orders if existing_orders is not None
                    else open_protective_stops(account, ticker))

        # Already correct -> leave it alone.
        for order in existing:
            try:
                same_price = abs(float(order.get("stop_price", 0) or 0) - stop_price) < 0.005
                same_qty = int(float(order.get("qty", 0) or 0)) == qty
            except (TypeError, ValueError):
                continue
            if same_price and same_qty and len(existing) == 1:
                return {"ticker": ticker, "status": "already_set", "order_id": order.get("id"),
                         "stop_price": stop_price, "qty": qty}

        # A stop at or above the current price is rejected by Alpaca (and
        # means the position is already through its hard floor) — leave the
        # book clean and let check_stops() report the breach for an
        # immediate market exit instead.
        if current_price > 0 and stop_price >= current_price:
            cancelled = cancel_protective_stops(account, ticker, wait=False, orders=existing)
            return {"ticker": ticker, "status": "below_stop_already",
                     "reason": f"stop ${stop_price:.2f} >= current ${current_price:.2f}; "
                               f"check-stops should exit this position now",
                     "cancelled": cancelled}

        cancelled = cancel_protective_stops(account, ticker, orders=existing) if existing else []
        order = place_stop_order(account, ticker, qty, stop_price)
        return {"ticker": ticker, "status": "placed", "order_id": order.get("id"),
                 "stop_price": stop_price, "qty": qty, "replaced": cancelled}
    except Exception as e:
        return {"ticker": ticker, "status": "error", "error": str(e)}


def reconcile_protective_stops(account: str) -> List[Dict[str, Any]]:
    """Pre-session / per-tick GTC audit for protective stops: every open
    position ends up with exactly one correctly-sized resting stop, and no
    stop is left resting on a ticker that is no longer held.

    strategy.md has carried a "pre-session GTC order audit — clear all
    stale/unfilled GTC orders before first tick, stale orders can silently
    block all exits" rule since v1.0 with no script behind it; this is that
    rule, mechanized, and extended to the stop orders introduced 2026-08-01.
    Run it from `--action check-stops` (every tick) and `--action sync-stops`
    (explicit pre-session audit).

    Never raises — returns one status dict per ticker acted on.
    """
    if not _protective_stops_enabled():
        return [{"status": "disabled"}]

    results = []
    try:
        positions = get_positions(account)
    except Exception as e:
        return [{"status": "error", "error": f"could not read positions: {e}"}]

    # One read of the whole open-order book, sliced per ticker — this runs
    # every tick, so it must not be one API call per position.
    stops_by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for order in open_protective_stops(account):
        stops_by_ticker.setdefault(str(order.get("symbol", "")).upper(), []).append(order)

    held = set()
    for p in positions:
        ticker = str(p.get("symbol", "")).upper()
        held.add(ticker)
        results.append(ensure_protective_stop(
            account, ticker, position=p, existing_orders=stops_by_ticker.get(ticker, [])))

    # Orphans: a resting sell stop whose position is gone (closed by hand,
    # closed by another process, or filled elsewhere). Left alone these are
    # exactly the "stale orders silently blocking exits" strategy.md warns
    # about — and would sell short if they ever triggered.
    for ticker, orders in stops_by_ticker.items():
        if ticker and ticker not in held:
            cancelled = cancel_protective_stops(account, ticker, wait=False, orders=orders)
            if cancelled:
                results.append({"ticker": ticker, "status": "orphan_cancelled", "cancelled": cancelled})
    return results


def reconcile_stopped_out_positions(account: str,
                                     positions: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Close the books on any position that left Alpaca without this file
    selling it — i.e. a broker-side protective stop that triggered while no
    tick was running, which is exactly the outage the resting stop exists
    to cover.

    Without this, the new stop order would create the very state drift the
    labeling fix is about: the position gone at the broker, still 'open' in
    the positions table, its training_examples row unlabeled forever, and
    bankroll/experience never told about the loss. Runs the same
    close_trade_outcome() + close_position() bookkeeping a normal SELL
    does, using the broker's own fill price.

    Only touches positions the local DB believes are open and Alpaca no
    longer holds, and close_position() flips status on the way out, so it
    can't process the same exit twice. Never raises.
    """
    results: List[Dict[str, Any]] = []
    try:
        if positions is None:
            positions = get_positions(account)
        held = {str(p.get("symbol", "")).upper() for p in positions}
    except Exception as e:
        return [{"status": "error", "error": f"could not read positions: {e}"}]

    try:
        conn = trader_db.get_conn()
        try:
            open_rows = trader_db.get_open_positions(conn)
        finally:
            conn.close()
    except Exception as e:
        return [{"status": "error", "error": f"could not read local positions: {e}"}]

    for row in open_rows:
        ticker = str(row.get("ticker", "")).upper()
        if not ticker or ticker in held:
            continue
        try:
            orders = get_closed_orders(account, ticker)
        except Exception as e:
            results.append({"ticker": ticker, "status": "unresolved",
                             "reason": f"could not read closed orders: {e}"})
            continue

        fill = None
        for o in (orders if isinstance(orders, list) else []):
            if (str(o.get("side", "")).lower() == "sell"
                    and str(o.get("status", "")).lower() == "filled"
                    and o.get("filled_avg_price")):
                fill = o
                break
        if fill is None:
            # Position gone but no fill to price it from — do NOT guess a
            # P&L into bankroll/experience off a stale entry price. Report
            # it and leave the row open for a human/agent to resolve.
            results.append({"ticker": ticker, "status": "unresolved",
                             "reason": "no filled SELL order found for a position Alpaca no longer holds"})
            continue

        try:
            exit_price = float(fill["filled_avg_price"])
            qty = int(float(fill.get("filled_qty") or row.get("shares") or 0))
            entry_price = float(row.get("entry_price") or 0)
            if qty < 1 or entry_price <= 0:
                results.append({"ticker": ticker, "status": "unresolved",
                                 "reason": "missing qty/entry price for reconciliation"})
                continue

            outcome = close_trade_outcome(account, ticker, entry_price, exit_price, qty,
                                           position_entry_time=row.get("entry_time"))
            now_iso = datetime.now(timezone.utc).isoformat()
            conn = trader_db.get_conn()
            try:
                trader_db.close_position(
                    conn, ticker=ticker, closed_at=now_iso,
                    close_reason=f"broker-side stop fill (order {fill.get('id')})",
                    realized_pnl=outcome["pnl"], realized_return_pct=outcome["return_pct"],
                )
            finally:
                conn.close()
            record_order_submitted(ticker, "SELL")
            results.append({"ticker": ticker, "status": "closed_from_broker_fill",
                             "exit_price": exit_price, "qty": qty,
                             "pnl": round(outcome["pnl"], 2),
                             "return_pct": round(outcome["return_pct"], 2),
                             "outcome_label_warning": outcome["outcome_label_warning"]})
        except Exception as e:
            results.append({"ticker": ticker, "status": "error", "error": str(e)})
    return results


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


def _size_cap_pct_for(risk: Dict[str, Any], play_type: Optional[str]) -> Tuple[float, str]:
    """The position-size cap (% of portfolio) that actually governs a given
    play type, plus a label for the reason string.

    2026-08-01: GATES is an ordered dict and check_order() returns on the
    first rejection, so gate_position_size (index 1) runs BEFORE
    gate_long_play (2) and gate_conviction_play (3). Applying the flat
    risk.max_position_pct here regardless of play type meant a conviction
    play sized to risk.conviction_play.position_size_pct was rejected by
    this gate before the gate that authorizes that size ever executed --
    confirmed live, `SELECT play_type, count(*) FROM positions` returned
    only {'standard': 41}, the conviction bucket had never fired once since
    shipping on 2026-07-30.

    Each bucket's own cap is read from params.json rather than assumed to
    be larger or smaller than the flat one, so this stays correct whatever
    the numbers are: long_play.position_size_pct is currently *smaller*
    than max_position_pct (a deliberately small unproven experiment) and
    conviction_play.position_size_pct is larger. gate_long_play /
    gate_conviction_play still enforce the same cap plus their concurrency
    limits -- this only stops an earlier gate from vetoing a size a later
    gate is there to allow."""
    if play_type == "conviction":
        cp = risk.get("conviction_play", {})
        return float(cp.get("position_size_pct", 10.0)), "conviction-play cap"
    if play_type == "long":
        lp = risk.get("long_play", {})
        return float(lp.get("position_size_pct", 3.0)), "long-play cap"
    return float(risk.get("max_position_pct", 6.0)), "cap"


def gate_position_size(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    risk = load_params().get("risk", {})
    max_pct, cap_label = _size_cap_pct_for(risk, action.get("play_type"))
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
            f"(existing ${existing_value:,.2f} + proposed ${proposed_value:,.2f}), "
            f"exceeds {max_pct:.0f}% {cap_label}"
        )
    return True, f"{ticker} at {total_pct:.1f}% of portfolio, within {max_pct:.0f}% {cap_label}"


def gate_long_play(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Enforces long-play-specific rules on BUYs tagged play_type='long'
    (params.json risk.long_play, 2026-07-30): a smaller position_size_pct
    cap than the normal max_position_pct (this is a live, unproven
    experiment -- see the long_play._source note in params.json for why),
    plus a max_concurrent_long_plays cap so it can't quietly become the
    default response to every entry. Non-long BUYs and all SELLs skip this
    gate entirely -- it has nothing to say about a standard position.
    """
    if action.get("action") != "BUY" or action.get("play_type") != "long":
        return True, "not a long play, skipped"

    lp_params = load_params().get("risk", {}).get("long_play", {})
    if not lp_params.get("enabled", True):
        return False, "long plays disabled via params.json risk.long_play.enabled"

    ticker = str(action.get("ticker", "")).upper()
    price = float(action.get("price", 0) or 0)
    qty = float(action.get("quantity", 0))
    proposed_value = qty * price
    portfolio_value = float(context.get("portfolio_value", 0))
    if proposed_value <= 0 or portfolio_value <= 0:
        return True, "no price/portfolio data, skipped (fail-open)"

    size_pct = float(lp_params.get("position_size_pct", 3.0))
    existing_value = sum(
        float(p.get("market_value", 0))
        for p in context.get("positions", [])
        if str(p.get("symbol", "")).upper() == ticker
    )
    total_pct = (existing_value + proposed_value) / portfolio_value * 100
    if total_pct > size_pct:
        return False, (
            f"{ticker} long play would be {total_pct:.1f}% of portfolio "
            f"(existing ${existing_value:,.2f} + proposed ${proposed_value:,.2f}), "
            f"exceeds long-play cap of {size_pct:.1f}% (smaller than normal max_position_pct while unproven)"
        )

    max_concurrent = int(lp_params.get("max_concurrent_long_plays", 2))
    try:
        conn = trader_db.get_conn()
        try:
            open_long_plays = [
                p for p in trader_db.get_open_positions(conn)
                if p.get("play_type") == "long" and str(p.get("ticker", "")).upper() != ticker
            ]
        finally:
            conn.close()
    except Exception as e:
        return True, f"could not check concurrent long plays (fail-open): {e}"

    if len(open_long_plays) >= max_concurrent:
        tickers = ", ".join(p["ticker"] for p in open_long_plays)
        return False, (
            f"already {len(open_long_plays)} open long play(s) ({tickers}), "
            f"at max_concurrent_long_plays cap of {max_concurrent}"
        )
    return True, (
        f"{ticker} long play at {total_pct:.1f}% of portfolio (cap {size_pct:.1f}%), "
        f"{len(open_long_plays)}/{max_concurrent} concurrent long plays"
    )


def gate_conviction_play(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Enforces conviction-play rules on BUYs tagged play_type='conviction'
    (params.json risk.conviction_play): a larger position_size_pct cap than
    the normal max_position_pct (this is the higher-conviction bucket, sized
    up not down), plus a max_concurrent_conviction_plays cap. Non-conviction
    BUYs and all SELLs skip this gate entirely.
    """
    if action.get("action") != "BUY" or action.get("play_type") != "conviction":
        return True, "not a conviction play, skipped"

    cp_params = load_params().get("risk", {}).get("conviction_play", {})
    if not cp_params.get("enabled", True):
        return False, "conviction plays disabled via params.json risk.conviction_play.enabled"

    ticker = str(action.get("ticker", "")).upper()
    price = float(action.get("price", 0) or 0)
    qty = float(action.get("quantity", 0))
    proposed_value = qty * price
    portfolio_value = float(context.get("portfolio_value", 0))
    if proposed_value <= 0 or portfolio_value <= 0:
        return True, "no price/portfolio data, skipped (fail-open)"

    size_pct = float(cp_params.get("position_size_pct", 10.0))
    existing_value = sum(
        float(p.get("market_value", 0))
        for p in context.get("positions", [])
        if str(p.get("symbol", "")).upper() == ticker
    )
    total_pct = (existing_value + proposed_value) / portfolio_value * 100
    if total_pct > size_pct:
        return False, (
            f"{ticker} conviction play would be {total_pct:.1f}% of portfolio "
            f"(existing ${existing_value:,.2f} + proposed ${proposed_value:,.2f}), "
            f"exceeds conviction-play cap of {size_pct:.1f}%"
        )

    max_concurrent = int(cp_params.get("max_concurrent_conviction_plays", 5))
    try:
        conn = trader_db.get_conn()
        try:
            open_conviction_plays = [
                p for p in trader_db.get_open_positions(conn)
                if p.get("play_type") == "conviction" and str(p.get("ticker", "")).upper() != ticker
            ]
        finally:
            conn.close()
    except Exception as e:
        return True, f"could not check concurrent conviction plays (fail-open): {e}"

    if len(open_conviction_plays) >= max_concurrent:
        tickers = ", ".join(p["ticker"] for p in open_conviction_plays)
        return False, (
            f"already {len(open_conviction_plays)} open conviction play(s) ({tickers}), "
            f"at max_concurrent_conviction_plays cap of {max_concurrent}"
        )
    return True, (
        f"{ticker} conviction play at {total_pct:.1f}% of portfolio (cap {size_pct:.1f}%), "
        f"{len(open_conviction_plays)}/{max_concurrent} concurrent conviction plays"
    )


def gate_max_portfolio_risk(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Portfolio-level stop-loss exposure: if every open position (plus this
    proposed buy) hit its hard stop simultaneously, what % of equity would be
    lost? stop_loss_pct is a single global value (risk.stop_loss_pct, applied
    uniformly to every position -- see gate_drawdown_circuit_breaker/check_stops),
    so this reduces to gross_exposure_value * stop_loss_pct / equity rather than
    needing a per-position stop distance."""
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    params = load_params()
    max_risk_pct = float(params.get("risk", {}).get("max_portfolio_risk_pct", 8.0))
    stop_loss_frac = abs(float(params.get("risk", {}).get("stop_loss_pct", -10.0))) / 100.0
    price = float(action.get("price", 0) or 0)
    qty = float(action.get("quantity", 0))
    proposed_value = qty * price
    portfolio_value = float(context.get("portfolio_value", 0))
    if proposed_value <= 0 or portfolio_value <= 0:
        return True, "no price/portfolio data, skipped (fail-open)"

    existing_value = sum(float(p.get("market_value", 0)) for p in context.get("positions", []))
    total_exposure_value = existing_value + proposed_value
    risk_pct = total_exposure_value * stop_loss_frac / portfolio_value * 100

    if risk_pct > max_risk_pct:
        return False, (
            f"portfolio stop-loss exposure would be {risk_pct:.1f}% of equity "
            f"(${total_exposure_value:,.2f} total position value x {stop_loss_frac*100:.0f}% stop), "
            f"exceeds {max_risk_pct:.0f}% cap"
        )
    return True, f"portfolio stop-loss exposure at {risk_pct:.1f}% of equity, within {max_risk_pct:.0f}% cap"


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
    """2026-07-28: was a positions/<ticker>.md 'Sector:' line read -- none
    of the real position files ever had one (no script wrote it, and the
    LLM agent never filled it in either), so this gate was silently
    fail-open in practice. Now reads the positions table, populated
    directly by main()'s BUY handler from --sector at open time."""
    try:
        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, ticker.upper())
        finally:
            conn.close()
    except Exception:
        return None
    return row["sector"] if row else None


def gate_sector_concentration(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Sector lookup reads the positions table (populated at BUY time from
    --sector). No sector data available -> skip (fail-open), not a block."""
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
    """Reject any BUY/SELL outside 09:30-16:00 ET, Mon-Fri, or on an NYSE
    holiday (fixed + floating, e.g. Thanksgiving/Easter-derived) or early
    close day (see market_hours.py -- a real calendar, not just a weekday
    check, so this doesn't try to trade on a market holiday that happens
    to fall Mon-Fri).

    context["_test_now"] lets tests inject a fixed timestamp instead of the
    real wall clock — not used in production, only by the test suite.
    """
    import datetime
    if context.get("_test_now") is not None:
        now = context["_test_now"]
    else:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            now = datetime.datetime.now()

    if now.weekday() >= 5:
        return False, f"{now.strftime('%A')} — market closed on weekends"

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import market_hours
    if market_hours.is_holiday(now.date()) or market_hours.is_custom_holiday(now.date()):
        return False, f"{now.strftime('%Y-%m-%d')} — market closed (holiday)"

    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=14, minute=0, second=0, microsecond=0) \
        if market_hours.is_early_close_day(now.date()) \
        else now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now < market_open or now > market_close:
        close_note = " (early close today)" if market_hours.is_early_close_day(now.date()) else ""
        return False, f"{now.strftime('%H:%M %Z')} — market open 09:30-{market_close.strftime('%H:%M')}{close_note}"
    return True, f"{now.strftime('%H:%M %Z')} — market open"


def _is_regular_trading_hours() -> bool:
    """True during 09:30-16:00 ET, Mon-Fri — same window as gate_hours.

    2026-07-27: the heartbeat calls the `status` action until 23:00 ET, well
    past the 09:30-16:00 ET `stonks-tick` cron window that actually trades.
    Anything driven by `status` that should only count real trading activity
    (deployment_pressure's tick counter) must check this first, or it
    inflates off-hours with no corresponding trades.
    """
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def gate_bankroll(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Reject BUYs that exceed bankroll.py's current self-calibrating ceiling.

    bankroll.py grows the ceiling 2%/win and shrinks it 1%/loss (accelerating
    above 55% win rate, decelerating below 45%) — this is what makes position
    sizing actually adapt to real performance, not just a fixed % of
    portfolio. See scripts/bankroll.py / bankroll.md.

    2026-07-23: the ceiling used here is now competition-mode adjusted
    (bankroll.effective_ceiling) — ramps up in the final ~60 days before
    the 12/31/26 deadline, and reacts to whether the account is ahead or
    behind its own starting capital. Raw ceiling growth/decay from wins
    and losses is unchanged; this only scales the number gate_bankroll
    actually compares against.

    2026-08-01: play-type aware, for the same reason gate_position_size is
    (see _size_cap_pct_for). The bankroll ceiling is a dollar amount, not a
    percentage, so it doesn't share max_position_pct's flat cap — but it
    independently vetoed the conviction bucket anyway: at $10.4k equity the
    ceiling was $679 while risk.conviction_play.position_size_pct (10%)
    authorizes ~$1,042, so every full-size conviction play would have been
    rejected here even after the position-size gate was fixed. A play
    type's own explicitly-configured size cap in params.json is a
    deliberate risk decision; the ceiling floors up to it rather than
    silently overriding it. Standard BUYs are unaffected — they compare
    against the raw ceiling exactly as before, and every other gate (cash,
    position size, max_portfolio_risk, drawdown) still applies to all play
    types.
    """
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    price = float(action.get("price", 0) or 0)
    qty = float(action.get("quantity", 0))
    cost = qty * price
    if cost <= 0:
        return True, "no price data, skipped (fail-open)"

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import bankroll
    state = bankroll.read_bankroll()
    portfolio_value = float(context.get("portfolio_value", 0) or 0)
    ceiling = (bankroll.effective_ceiling(state, portfolio_value)
               if portfolio_value > 0 else state["ceiling"])

    play_type = action.get("play_type")
    note = ""
    if play_type in ("conviction", "long") and portfolio_value > 0:
        risk = load_params().get("risk", {})
        play_cap_pct, cap_label = _size_cap_pct_for(risk, play_type)
        play_cap_value = portfolio_value * play_cap_pct / 100.0
        if play_cap_value > ceiling:
            ceiling = play_cap_value
            note = f" ({cap_label} {play_cap_pct:.1f}% of portfolio floors the ceiling for this play type)"

    if cost > ceiling:
        return False, f"BUY costs ${cost:,.2f}, exceeds bankroll ceiling ${ceiling:,.2f}{note}"
    return True, f"BUY costs ${cost:,.2f}, within bankroll ceiling ${ceiling:,.2f}{note}"


def gate_conviction(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """A sanity floor, not a real gate -- catches a broken/zero conviction
    score. Entry quality is decided by the gestalt Stan reasons over (world
    narrative, congress trades, fundamentals, wiki, cross-sectional
    momentum), not a numeric threshold formula."""
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    if action.get("conviction") is None:
        return True, "no conviction passed, skipped (fail-open)"
    risk = load_params().get("risk", {})
    floor = float(risk.get("conviction_floor", 0.10))
    conviction = float(action["conviction"])
    if conviction < floor:
        return False, f"conviction {conviction:.2f} below sanity floor {floor:.2f}"
    return True, f"conviction {conviction:.2f} >= {floor:.2f} floor"


def _load_recent_orders() -> Dict[str, Any]:
    if RECENT_ORDERS_PATH.exists():
        try:
            return json.loads(RECENT_ORDERS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _load_experience() -> Dict[str, Any]:
    """experience.json's total_trades/total_wins/total_losses/consecutive_*
    fields (NOT current_level/milestones_unlocked -- those were genuinely
    superseded by bankroll.py's UNLOCK_TIERS, see that module's 2026-07-23
    comment) used to be hand-edited by the LLM every tick per TOOLS.md's
    prose instruction ("read each tick, update ... after trades"). That held
    up for a week, then silently stopped: 2026-07-27's 3 morning SELL exits
    (F/IP/FHB) never bumped total_trades even though total_ticks kept
    incrementing normally the same day -- same "prose reminder eventually
    fails" pattern as the pre-2026-07-22 outcome-labeling gap this file's
    close_trade_outcome() already fixed the same way. Confirmed still a
    live-read field, not dead: Stan's own HEARTBEAT.md self-stats line
    quotes it every tick ("self stats: 0 trades logged today..."). Fixed by
    mechanizing the update into the same two hook points bankroll.py
    already updates reliably from -- record_order_submitted() (every real
    order) and close_trade_outcome() (every real SELL's win/loss)."""
    defaults = {
        "version": 1, "total_ticks": 0, "total_trades": 0,
        "total_wins": 0, "total_losses": 0,
        "consecutive_wins": 0, "consecutive_losses": 0,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if EXPERIENCE_PATH.exists():
        try:
            state = json.loads(EXPERIENCE_PATH.read_text())
            defaults.update(state)
            return defaults
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


def _save_experience(state: Dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    EXPERIENCE_PATH.write_text(json.dumps(state, indent=2))


def record_experience_trade() -> None:
    """Bumps total_trades once for any order that actually executed (BUY or
    SELL) -- called from record_order_submitted(), the one point in this
    file that already means "a real order was just placed." Matches the
    historical hand-maintained semantics (total_trades counted every
    executed order, not just closes -- see git history of experience.json,
    e.g. a solo BUY bumping total_trades with no win/loss change). Fails
    open (never raises) -- a bookkeeping write failure must not look like a
    failed order; the order already executed by the time this runs, same
    reasoning as close_trade_outcome's Postgres fail-open path below."""
    try:
        state = _load_experience()
        state["total_trades"] = int(state.get("total_trades", 0)) + 1
        _save_experience(state)
    except OSError:
        pass


def record_experience_outcome(is_win: bool) -> None:
    """Bumps total_wins/total_losses + the consecutive-streak counters,
    called from close_trade_outcome() once a SELL's pnl is known -- the
    same trigger point bankroll.recalc_ceiling() already updates from
    reliably. Fails open, same reasoning as record_experience_trade()."""
    try:
        state = _load_experience()
        if is_win:
            state["total_wins"] = int(state.get("total_wins", 0)) + 1
            state["consecutive_wins"] = int(state.get("consecutive_wins", 0)) + 1
            state["consecutive_losses"] = 0
        else:
            state["total_losses"] = int(state.get("total_losses", 0)) + 1
            state["consecutive_losses"] = int(state.get("consecutive_losses", 0)) + 1
            state["consecutive_wins"] = 0
        _save_experience(state)
    except OSError:
        pass


def record_order_submitted(ticker: str, action: str, today: Optional[str] = None) -> None:
    """Called after a BUY/SELL actually executes — used by gate_duplicate_order
    to block an accidental repeat submission of the same ticker+action within
    the cooldown window. Confirmed 2026-07-22: DVN got bought 3 times in one
    tick (three separate BUY orders, 5-7 seconds apart) — nothing stopped the
    agent from submitting the same order again without noticing the first one
    had already filled.

    Also bumps the daily order counter gate_daily_order_count reads, and
    experience.json's total_trades (2026-07-27, see _load_experience's
    docstring) — `today` lets tests inject a fixed date; production always
    computes the real one."""
    state = _load_recent_orders()
    state[f"{ticker.upper()}:{action.upper()}"] = time.time()
    STATE_DIR.mkdir(exist_ok=True)
    RECENT_ORDERS_PATH.write_text(json.dumps(state, indent=2))

    if today is None:
        today = _today_et({})
    _record_daily_order(today)
    record_experience_trade()


def gate_duplicate_order(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Reject a BUY or SELL for the same ticker+action submitted again within
    risk.duplicate_order_cooldown_seconds of the last one that actually
    executed. Applies to both BUY and SELL, unlike most gates — a duplicate
    SELL is just as real a mistake as a duplicate BUY."""
    ticker = str(action.get("ticker", "")).upper()
    side = str(action.get("action", "")).upper()
    if not ticker or side not in ("BUY", "SELL"):
        return True, "no ticker/action, skipped"

    cooldown = float(load_params().get("risk", {}).get("duplicate_order_cooldown_seconds", 60))
    state = _load_recent_orders()
    last_ts = state.get(f"{ticker}:{side}")
    if last_ts is None:
        return True, "no recent matching order, skipped"

    elapsed = time.time() - float(last_ts)
    if elapsed < cooldown:
        return False, f"{side} {ticker} submitted {elapsed:.0f}s ago (< {cooldown:.0f}s cooldown) — likely a duplicate"
    return True, f"last {side} {ticker} was {elapsed:.0f}s ago, outside {cooldown:.0f}s cooldown"


def gate_order_idempotency(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Authoritative, cross-process duplicate-order guard — queries Alpaca's
    own live order state directly (GET /v2/orders?status=open&symbols=X)
    instead of local file state.

    Why this exists alongside gate_duplicate_order: that gate is a local,
    file-based, check-then-set mechanism (state/recent_orders.json) — it
    only knows about orders THIS process already recorded. Two genuinely
    concurrent processes (a cron tick and position_stream.py's websocket
    daemon in paper-trading-rebuild, both invoking this same
    check_order()/place_order() path via set_watch.py-triggered execution)
    can each read "nothing recorded yet" before either writes its own
    record — a classic TOCTOU race. gate_duplicate_order is also purely
    time-window-based (60s cooldown), not "is there an actual live order
    right now." Confirmed twice: KRC bought 2x on 2026-07-27 and IP bought
    2x on 2026-07-24, both from overlapping sessions racing the same local
    check. Querying Alpaca directly closes that gap — it's authoritative
    regardless of which process is asking, because Alpaca is the one
    shared source of truth every process ultimately talks to. This is a
    second, independent layer, not a replacement — keep gate_duplicate_order
    as-is (it's free, no API call, and catches same-process repeats before
    this gate's network round-trip is even needed).

    BUY-only, unlike gate_duplicate_order (which guards both sides). A
    duplicate SELL of the same ticker fails harmlessly at Alpaca — you
    can't sell shares you don't hold (or already sold), the second order
    is rejected by the exchange itself with no capital consequence. A
    duplicate BUY compounds: both fill, and the account now holds double
    the intended size (exactly the KRC/IP failure mode this closes). No
    reason to spend the extra API call guarding a failure mode Alpaca
    already prevents for free.

    Fails open on any Alpaca API error (network hiccup, auth issue, rate
    limit, missing account in context) — matches every other gate's
    convention: a data hiccup shouldn't block a trade, only a genuine,
    confirmed open order should.
    """
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    ticker = str(action.get("ticker", "")).upper()
    if not ticker:
        return True, "no ticker, skipped"
    account = context.get("account")
    if not account:
        return True, "no account in context, skipped (fail-open)"

    try:
        open_orders = get_open_orders(account, ticker)
    except Exception as e:
        return True, f"could not query Alpaca open orders (fail-open): {e}"

    matching = [
        o for o in open_orders
        if str(o.get("symbol", "")).upper() == ticker and str(o.get("side", "")).upper() == "BUY"
    ]
    if matching:
        order_ids = ", ".join(str(o.get("id", "?")) for o in matching)
        return False, (
            f"{ticker} already has {len(matching)} open BUY order(s) at Alpaca ({order_ids}) "
            f"— refusing duplicate submission"
        )
    return True, f"{ticker} has no open BUY orders at Alpaca, safe to submit"


def _today_et(context: Dict[str, Any]) -> str:
    """YYYY-MM-DD in America/New_York, matching gate_hours' pattern.
    context["_test_now"] lets tests inject a fixed date instead of the
    real wall clock — not used in production."""
    if context.get("_test_now") is not None:
        return context["_test_now"].strftime("%Y-%m-%d")
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d")


def _load_daily_order_count(today: str) -> int:
    """Count of orders submitted today (ET), across all tickers/actions —
    resets automatically when the stored date no longer matches today,
    same rollover-by-comparison pattern as everything else in this file
    (no separate midnight-reset job needed)."""
    if not DAILY_ORDER_COUNT_PATH.exists():
        return 0
    try:
        state = json.loads(DAILY_ORDER_COUNT_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    if state.get("date") != today:
        return 0
    return int(state.get("count", 0))


def _record_daily_order(today: str) -> None:
    count = _load_daily_order_count(today) + 1
    STATE_DIR.mkdir(exist_ok=True)
    DAILY_ORDER_COUNT_PATH.write_text(json.dumps({"date": today, "count": count}, indent=2))


def gate_daily_order_count(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """2026-07-23: strategy.md's Risk Management section has promised a
    'daily order-count audit' (risk_guards) since v1.0 — a rogue-trading
    backstop (a stuck loop or repeated bad decisions placing far more
    orders than a small-cap/wide/diversified strategy should in one day)
    — but nothing ever mechanically enforced it (found by
    workspace_review.py's dead-param check: risk_guards.
    order_count_audit_threshold_daily was declared, never read). Applies
    to both BUY and SELL, same reasoning as gate_duplicate_order — a rogue
    loop isn't only a buying problem.
    """
    side = str(action.get("action", "")).upper()
    if side not in ("BUY", "SELL"):
        return True, "non-BUY/SELL, skipped"

    threshold = int(load_params().get("risk_guards", {}).get("order_count_audit_threshold_daily", 10))
    today = _today_et(context)
    count = _load_daily_order_count(today)
    if count >= threshold:
        return False, f"{count} orders already placed today (>= {threshold} threshold) — possible rogue loop"
    return True, f"{count}/{threshold} orders placed today"


def _load_peak_equity() -> float:
    if not PEAK_EQUITY_PATH.exists():
        return 0.0
    try:
        return float(json.loads(PEAK_EQUITY_PATH.read_text()).get("peak_equity", 0.0))
    except (json.JSONDecodeError, OSError, ValueError):
        return 0.0


def _update_peak_equity(current_equity: float) -> float:
    """Ratchets state/peak_equity.json up only, same pattern as check_stops'
    trailing-stop high-water mark. Called both from the drawdown gate (every
    BUY/SELL attempt) and the `status` action (every tick, even HOLD-only
    ones) so the recorded peak doesn't lag behind a real new high reached on
    a day with no order attempts."""
    peak = max(_load_peak_equity(), current_equity)
    STATE_DIR.mkdir(exist_ok=True)
    PEAK_EQUITY_PATH.write_text(json.dumps({"peak_equity": peak}, indent=2))
    return peak


def gate_drawdown_circuit_breaker(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Portfolio-level circuit breaker, adapted from
    paper-trading-rebuild/COMPETITION.md's >15%/>20% drawdown framework:
    pauses new BUYs once equity has fallen risk.drawdown_pause_pct from its
    recorded peak, hard-halts new BUYs at risk.drawdown_halt_pct pending
    review. Deliberately BUY-only at both thresholds — unlike
    COMPETITION.md's "eliminated" framing, this never blocks a SELL
    (including stop-loss exits): an account can't recover by being unable to
    cut a losing position at the exact moment it's furthest underwater. "An
    account at zero doesn't win anything" (SOUL.md) cuts against blocking
    exits, not for it.
    """
    portfolio_value = float(context.get("portfolio_value", 0) or 0)
    if portfolio_value <= 0:
        return True, "no portfolio value data, skipped (fail-open)"

    peak = _update_peak_equity(portfolio_value)
    drawdown_pct = ((peak - portfolio_value) / peak * 100) if peak > 0 else 0.0

    if action.get("action") != "BUY":
        return True, f"non-BUY, unaffected (drawdown {drawdown_pct:.1f}% from peak ${peak:,.2f})"

    risk = load_params().get("risk", {})
    pause_pct = float(risk.get("drawdown_pause_pct", 15.0))
    halt_pct = float(risk.get("drawdown_halt_pct", 20.0))

    if drawdown_pct >= halt_pct:
        return False, (f"HALT: drawdown {drawdown_pct:.1f}% from peak ${peak:,.2f} "
                        f">= {halt_pct}% — new entries blocked pending review")
    if drawdown_pct >= pause_pct:
        return False, (f"PAUSED: drawdown {drawdown_pct:.1f}% from peak ${peak:,.2f} "
                        f">= {pause_pct}% — new entries paused")
    return True, f"drawdown {drawdown_pct:.1f}% from peak ${peak:,.2f}, within bounds"


# name -> gate function. params.json guardrail_gates.<name> = false disables it.
GATES = {
    "cash": gate_cash,
    "position_size": gate_position_size,
    "long_play": gate_long_play,
    # params.json risk.profit_target_is_guide and risk.conviction_floor_min are
    # referenced here so workspace_review.py's dead-param check finds them.
    "conviction_play": gate_conviction_play,
    "max_portfolio_risk": gate_max_portfolio_risk,
    "max_positions": gate_max_positions,
    "sector_concentration": gate_sector_concentration,
    "hours": gate_hours,
    "conviction": gate_conviction,
    "bankroll": gate_bankroll,
    "duplicate_order": gate_duplicate_order,
    "order_idempotency": gate_order_idempotency,
    "order_count_audit": gate_daily_order_count,
    "drawdown_circuit_breaker": gate_drawdown_circuit_breaker,
}


ORDER_LOCK_DIR = STATE_DIR / "order_locks"


@contextlib.contextmanager
def _order_lock(ticker: str):
    """Cross-process advisory lock, keyed by ticker, held across the
    check_order() -> place_order() critical section.

    gate_order_idempotency (2026-07-27) queries Alpaca's live open-order
    book right before submission, but that alone doesn't close the race:
    two processes (two overlapping ticks, or a tick racing a triggered
    set_watch.py watch) can both run that query within the same short
    window, both see "nothing open yet" — because neither order has landed
    at Alpaca yet — and both submit. Confirmed in production: BFST
    double-bought 2026-07-29, two days after that gate shipped, following
    the same pattern as IP (7/24) and KRC (7/27). This lock makes the
    check-then-submit pair atomic across processes, which an Alpaca query
    alone cannot do. Same fcntl pattern as set_watch.py's _locked().
    """
    ORDER_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = ORDER_LOCK_DIR / f"{ticker.upper()}.lock"
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def run_gates(context: Dict[str, Any], trade_action: Dict[str, Any],
              toggles: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Run a proposed trade through all enabled gates against the given
    context/trade_action. First rejection stops the chain. Pure function of
    its arguments -- no Alpaca calls, no I/O beyond reading params.json for
    default toggles when none are passed.

    2026-08-01: extracted out of check_order() so a simulated/historical
    caller (scripts/replay_order.py, the backtest-session order path) can
    drive the exact same gate chain against a backtest-session context
    without ever building a live-Alpaca dependency. check_order() below is
    now a thin live-context wrapper around this; zero behavior change for
    live trading."""
    if toggles is None:
        toggles = load_params().get("guardrail_gates", {})
    results = []
    for name, gate_fn in GATES.items():
        mode = toggles.get(name, True)
        if mode is False:
            results.append({"gate": name, "passed": True, "reason": "disabled via params.json guardrail_gates"})
            continue
        try:
            passed, reason = gate_fn(context, trade_action)
        except Exception as e:
            passed, reason = True, f"ERROR (fail-open): {e}"
        warn_only = (mode == "warn")
        entry = {"gate": name, "passed": passed or warn_only, "reason": reason}
        if warn_only and not passed:
            entry["warn_only"] = True
            entry["would_have_blocked"] = True
        results.append(entry)
        if not passed and not warn_only:
            return False, f"Blocked by {name}: {reason}", results
    return True, "All gates passed", results


def check_order(account: str, action: str, ticker: str, qty: int, price: Optional[float] = None,
                 conviction: Optional[float] = None, sector: Optional[str] = None,
                 play_type: Optional[str] = None) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Run a proposed trade through all enabled gates. First rejection stops the chain."""
    account_data = get_account(account)
    positions = get_positions(account)
    context = {
        "portfolio_value": float(account_data.get("equity", 0)),
        "cash": float(account_data.get("cash", 0)),
        "positions": [{"symbol": p["symbol"], "market_value": float(p["market_value"])} for p in positions],
        "account": account,  # gate_order_idempotency needs this to query Alpaca's live order book
    }
    trade_action = {
        "action": action.upper(), "ticker": ticker.upper(), "quantity": qty,
        "price": price, "conviction": conviction, "sector": sector, "play_type": play_type,
    }
    return run_gates(context, trade_action)


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


def _fetch_vol_20d(account: str, tickers: List[str]) -> Dict[str, float]:
    """Fetch 20-day daily-bar volatility (std of daily returns) for each
    ticker from Alpaca. Returns {ticker: vol_20d} dict — tickers with
    insufficient history (< 20 bars or all-zero returns) get vol_20d=0.0
    (falls back to the flat trailing_stop_pct base).

    Uses the same urllib.request pattern as other Alpaca calls in this file.
    Called exclusively by check_stops() when trailing_stop_mode='volatility_scaled'.
    Best-effort: never raises, returns empty dict on API error.
    """
    import urllib.request
    import urllib.parse
    from datetime import datetime, timezone, timedelta

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=60)  # 60 calendar days to guarantee ~40 trading days
    params = urllib.parse.urlencode({
        "symbols": ",".join(tickers),
        "timeframe": "1Day",
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "feed": "iex",
        "limit": 30,
    })
    url = f"{ALPACA_BASE_URL}/v2/stocks/bars?{params}"
    req = urllib.request.Request(url, headers=get_headers(account))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return {}

    result: Dict[str, float] = {}
    for ticker in tickers:
        bars = data.get("bars", {}).get(ticker, [])
        if len(bars) < 20:
            result[ticker] = 0.0
            continue
        closes = [b.get("c", 0.0) for b in bars]
        returns = [(closes[i] - closes[i-1]) / closes[i-1]
                    for i in range(1, len(closes)) if closes[i-1] > 0]
        if len(returns) < 20:
            result[ticker] = 0.0
            continue
        # Sample std (ddof=1), same as replay_check.py's vol_20d using rolling(20).std()
        import statistics
        vol = statistics.stdev(returns[-20:])
        result[ticker] = vol if vol > 0 else 0.0
    return result


def check_stops(account: str) -> List[Dict[str, Any]]:
    """Scan open positions for hard-stop, trailing-stop, or oversized-position
    breaches.

    Hard stop: risk.stop_loss_pct below entry price (fixed floor).
    Trailing stop: risk.trailing_stop_pct below the highest price observed
    since entry (ratchets up only; persisted to state/guardrail_stops.json).
    2026-07-27: briefly replaced with stop_patience.py (widening distances
    the longer a position is held) same day, then REVERTED same night —
    overnight split-window backtest (research/2026-07-27.md) found flat
    stops beat every patience variant tested (shipped params + 2 alternate
    ramp/bound speeds) on Sharpe and return, in both halves. Not a "wrong
    numbers" problem: rescued 6 trades, made 9 worse, net P&L down
    $268.58->$212.87 on the tested universe/window. stop_patience.py stays
    on disk (tests intact) for a future redesign, just unwired from here.
    Oversized: current market_value / portfolio_value exceeds
    risk.max_position_pct — this happens when a position grows past the cap
    via price appreciation (gate_position_size only blocks new BUYs from
    crossing the cap, it doesn't correct an existing position that's already
    over). Confirmed 2026-07-22: NVDA sat over cap 4 days / 3 nightly cycles
    with "trim it" as a journaled intention that never executed — mechanized
    here instead of relying on that being remembered again.
    Any of the three can be disabled via guardrail_gates.hard_stop /
    .trailing_stop / .position_size_trim.

    Returns breach dicts: {ticker, stop_type, reason, loss_pct, shares_to_sell
    (oversized only)}. Caller (tick loop) is responsible for actually
    executing the SELL.
    """
    params = load_params()
    toggles = params.get("guardrail_gates", {})
    risk = params.get("risk", {})
    hard_stop_pct = abs(float(risk.get("stop_loss_pct", -10.0)))
    trailing_pct = float(risk.get("trailing_stop_pct", 5.0))
    max_position_pct = float(risk.get("max_position_pct", 6.0))
    trailing_stop_mode = risk.get("trailing_stop_mode", "flat")
    trail_k = float(risk.get("trail_k", 40.0))
    trail_min_pct = float(risk.get("trail_min_pct", 4.0))
    trail_max_pct = float(risk.get("trail_max_pct", 12.0))

    positions = get_positions(account)
    portfolio_value = None
    if toggles.get("position_size_trim", True):
        account_data = get_account(account)
        portfolio_value = float(account_data.get("equity", 0))

    state = _load_stop_state()
    breaches = []
    held_tickers = set()

    # Pre-fetch vol_20d for ALL held tickers once (not per-position loop)
    vol_20d_map: Dict[str, float] = {}
    if toggles.get("trailing_stop", True) and trailing_stop_mode == "volatility_scaled":
        held = [p["symbol"].upper() for p in positions]
        if held:
            vol_20d_map = _fetch_vol_20d(account, held)

    # Pre-fetch long-play metadata (play_type/predicted_by_date) for ALL
    # held tickers once -- Alpaca's get_positions() above has no notion of
    # this, it's local trader_db.py state (2026-07-30, params.json
    # risk.long_play). Fail-open: a DB error here just means no ticker
    # gets long-play treatment this tick, falls through to normal stops.
    long_play_map: Dict[str, Dict[str, Any]] = {}
    conviction_play_tickers: set = set()
    if toggles.get("long_play", True) or toggles.get("conviction_play", True):
        try:
            conn = trader_db.get_conn()
            try:
                for row in trader_db.get_open_positions(conn):
                    if row.get("play_type") == "long" and row.get("predicted_by_date"):
                        long_play_map[str(row["ticker"]).upper()] = row
                    elif row.get("play_type") == "conviction":
                        conviction_play_tickers.add(str(row["ticker"]).upper())
            finally:
                conn.close()
        except Exception:
            long_play_map = {}
            conviction_play_tickers = set()
    today = datetime.now(timezone.utc).date()

    for p in positions:
        ticker = p["symbol"].upper()
        held_tickers.add(ticker)
        entry_price = float(p["avg_entry_price"])
        current_price = float(p["current_price"])
        market_value = float(p["market_value"])
        qty_held = int(float(p["qty"]))

        # Long-play resolution / trailing-stop exemption (2026-07-30,
        # params.json risk.long_play). A long play is exempt from the
        # normal trailing-stop schedule ONLY while its predicted_by_date
        # hasn't arrived yet -- the hard stop below still applies
        # unconditionally regardless, that circuit breaker is never
        # optional (see stop_patience.py's revert for why: letting
        # optimism override the true floor is exactly how blind patience
        # loses money). Once the date arrives, resolve mechanically right
        # here -- flips play_type back to 'standard' (so this fires once,
        # not every tick after) and labels the training_examples row so
        # hit rate is queryable later -- it does NOT force a sell; the
        # position just reverts to being judged on its own merits by the
        # normal stop schedule from this point on.
        is_active_long_play = False
        lp = long_play_map.get(ticker)
        if lp:
            try:
                deadline = datetime.strptime(lp["predicted_by_date"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                deadline = None
            if deadline and today < deadline:
                is_active_long_play = True
            elif deadline and today >= deadline:
                return_pct = (current_price - entry_price) / entry_price * 100 if entry_price else 0.0
                hit = return_pct > 0
                breaches.append({
                    "ticker": ticker, "stop_type": "long_play_resolved",
                    "reason": f"{ticker}: long play horizon reached (predicted_by_date {lp['predicted_by_date']}) -- "
                              f"prediction {'correct' if hit else 'incorrect'}, {return_pct:+.1f}% vs entry "
                              f"(\"{lp.get('prediction_reason', '')}\"). Reverting to standard trailing-stop schedule.",
                    "loss_pct": return_pct,
                    "long_play_hit": hit,
                })
                now_iso = datetime.now(timezone.utc).isoformat()
                try:
                    conn = trader_db.get_conn()
                    try:
                        trader_db.resolve_long_play(conn, ticker=ticker, updated_at=now_iso)
                        # Entry row only (2026-08-01) -- same fix as
                        # record_trade_close: "newest unlabeled row" would
                        # happily label a SELL/HOLD row that never carried
                        # the prediction being scored here.
                        te_row = trader_db.find_entry_training_example(
                            conn, ticker, position_entry_time=lp.get("entry_time"))
                        te_id = te_row["id"] if te_row else None
                        if te_id is not None:
                            trader_db.label_training_example(
                                conn, training_example_id=te_id, trade_id=None,
                                label_win=1 if hit else 0, label_return_pct=return_pct,
                                label_horizon="long_play_prediction",
                            )
                    finally:
                        conn.close()
                except Exception:
                    pass  # best-effort, matches the fail-open philosophy elsewhere in this function

        if portfolio_value and portfolio_value > 0 and current_price > 0:
            current_pct = market_value / portfolio_value * 100
            if current_pct > max_position_pct:
                target_value = portfolio_value * max_position_pct / 100
                excess_value = market_value - target_value
                shares_to_sell = min(qty_held, max(1, math.ceil(excess_value / current_price)))
                breaches.append({
                    "ticker": ticker, "stop_type": "oversized",
                    "reason": f"{ticker}: {current_pct:.1f}% of portfolio exceeds {max_position_pct:.0f}% cap "
                              f"(${market_value:,.2f} of ${portfolio_value:,.2f}) — trim {shares_to_sell} share(s)",
                    "loss_pct": (current_price - entry_price) / entry_price * 100 if entry_price else 0.0,
                    "shares_to_sell": shares_to_sell,
                })
                # Oversized is a trim, not an exit — still check hard/trailing
                # stops below, don't skip them the way a full-exit breach does.

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

        if toggles.get("trailing_stop", True) and not is_active_long_play:
            # 2026-07-30: vol-scaled trailing stop (trailing_stop_mode='volatility_scaled')
            # uses the same formula as replay_check.py's make_trader(vol_scaled_trail=True):
            #   trail_pct = trailing_stop_pct * (1 + trail_k * vol_20d), clamped to [trail_min_pct, trail_max_pct]
            # Research: 2026-07-28 trailing-stop.md — TRAIL_K=40 is best variant.
            # vol_20d_map is pre-fetched once above, not fetched per-position.
            effective_trail_pct = trailing_pct
            if trailing_stop_mode == "volatility_scaled":
                vol = vol_20d_map.get(ticker, 0.0)
                if vol > 0:
                    effective_trail_pct = max(trail_min_pct, min(trail_max_pct,
                                               trailing_pct * (1 + trail_k * vol)))
            if ticker in conviction_play_tickers:
                # Wider room for a short-term dip that doesn't break the
                # thesis -- not exempt from the trailing stop like a long
                # play, just less trigger-happy (params.json risk.
                # conviction_play.trail_multiplier).
                trail_multiplier = float(risk.get("conviction_play", {}).get("trail_multiplier", 1.5))
                effective_trail_pct = effective_trail_pct * trail_multiplier
            trail_stop_price = peak_price * (1 - effective_trail_pct / 100)
            if current_price <= trail_stop_price:
                drop_from_peak = (current_price - peak_price) / peak_price * 100
                breaches.append({
                    "ticker": ticker, "stop_type": "trailing",
                    "reason": f"{ticker}: trailing stop breached, {drop_from_peak:.1f}% off peak "
                              f"${peak_price:.2f} (stop ${trail_stop_price:.2f}, current ${current_price:.2f}, "
                              f"mode={trailing_stop_mode}, effective_trail={effective_trail_pct:.1f}%)",
                    "loss_pct": (current_price - entry_price) / entry_price * 100,
                })

    for ticker in list(state.keys()):
        if ticker not in held_tickers:
            del state[ticker]
    _save_stop_state(state)
    return breaches


# ─────────────────────────────────────────────────────────────────────────────
# Post-SELL bookkeeping — bankroll ceiling + Postgres outcome label
# ─────────────────────────────────────────────────────────────────────────────


def close_trade_outcome(account: str, ticker: str, entry_price: float, exit_price: float, qty: int,
                         position_entry_time: Optional[str] = None) -> Dict[str, Any]:
    """Called once a SELL actually executes. Updates bankroll.py's
    win/loss-adaptive ceiling, experience.json's total_wins/total_losses/
    consecutive-streak counters (2026-07-27, see _load_experience's
    docstring), AND labels the Postgres training_examples outcome
    (trading.training_examples.label_win/label_return_pct) — this used to
    be two separate concerns, with the Postgres label depending on the LLM
    remembering a second manual `record_decision.py close` call per
    tick_prompt.md step 9. Confirmed 2026-07-22: roughly half of real
    closed trades that week never got labeled this way. Mechanized here
    instead, same trigger point as the bankroll update, which was already
    reliable.

    Fail-open on the Postgres side — a labeling failure shouldn't look like
    a trade failure, the order already executed by the time this runs.
    experience.json bookkeeping is likewise fail-open internally (see
    record_experience_outcome).

    2026-08-01: position_entry_time (positions.entry_time of the position
    being closed) is passed straight through to record_trade_close as the
    correlation key for WHICH training_examples row gets the label. Without
    it, labeling fell back to "the newest unlabeled row for this ticker",
    which on a SELL is the SELL's own row — so BUY rows carrying the actual
    predictive signals sat unlabeled forever and the scorecard learned
    nothing. See decisions.record_trade_close.
    """
    pnl = (exit_price - entry_price) * qty
    return_pct = (exit_price - entry_price) / entry_price * 100 if entry_price else 0.0

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import bankroll
    state = bankroll.read_bankroll()
    bankroll.recalc_ceiling(state, pnl, is_win=(pnl > 0))
    bankroll.write_bankroll(state)
    record_experience_outcome(is_win=(pnl > 0))

    outcome_label_warning = None
    try:
        import decisions
        close_result = decisions.record_trade_close(
            trader_id=account, ticker=ticker, trade_id=None,
            pnl=pnl, return_pct=return_pct,
            position_entry_time=position_entry_time,
        )
        if "error" in close_result:
            outcome_label_warning = close_result["error"]
    except Exception as e:
        outcome_label_warning = f"could not label trade outcome: {e}"

    return {"pnl": pnl, "return_pct": return_pct, "outcome_label_warning": outcome_label_warning}


def _record_decision_row(action: str, ticker: str, conviction, rationale: str,
                          features: Dict[str, Any], regime: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Mechanized `decisions` table write, called from the BUY/SELL paths
    below on every real fill. Previously the only writer of this table was
    the standalone `record_decision.py decision` CLI, a second manual step
    per tick_prompt.md step 9 distinct from the executor call itself —
    confirmed stopped firing reliably after 2026-07-31 (decisions table: 34
    rows, all before then, while real trades kept happening and
    experience.json's total_trades -- mechanized separately, see
    _load_experience's docstring -- kept incrementing normally). Same
    root-cause pattern as that fix and as close_trade_outcome's Postgres
    labeling above, applied to the one table neither of those touches.

    Fail-open, same philosophy as close_trade_outcome/record_entry_example
    above — a logging failure must never look like a failed order, the
    order has already executed by the time this runs.
    """
    try:
        import decisions
        result = decisions.record_decision(
            trader_id="stonks", ticker=ticker, action=action,
            rationale=rationale or "", conviction=conviction if conviction is not None else 0.0,
            regime=regime, features=features or {},
        )
        if result.get("error"):
            print(json.dumps({"warning": f"decision log failed: {result['error']}"}), file=sys.stderr)
        return result
    except Exception as e:
        print(json.dumps({"warning": f"decision log failed: {e}"}), file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Alpaca order executor with built-in guardrails")
    parser.add_argument("--account", default="stonks", choices=["stonks"])
    parser.add_argument("--action", choices=["BUY", "SELL", "status", "check-stops", "sync-stops"])
    parser.add_argument("--ticker")
    parser.add_argument("--qty", type=int)
    parser.add_argument("--price", type=float, help="current/estimated price, used by guardrail checks")
    parser.add_argument("--conviction", type=float, help="0-1, used by the conviction gate on BUY")
    parser.add_argument("--sector", help="used by the sector-concentration gate; also persisted to positions on BUY")
    parser.add_argument("--thesis", help="why this trade -- reuse the same rationale passed to record_decision.py. "
                         "Persisted to the positions table on BUY. Soft-required: a missing thesis logs a warning, "
                         "never blocks the order.")
    parser.add_argument("--close-reason", help="SELL only -- why (breach type, thesis break, manual). "
                         "Defaults to a generic 'manual SELL' if omitted.")
    parser.add_argument("--play-type", default="standard", choices=["standard", "long", "conviction"],
                         help="BUY only -- 'long' tags this as a long play (params.json risk.long_play): "
                         "smaller size, exempt from the trailing-stop schedule until --predicted-by-date, "
                         "the hard stop still applies. Requires --predicted-by-date and --prediction-reason. "
                         "'conviction' tags this as a conviction play (params.json risk.conviction_play): "
                         "larger size, wider trailing stop, held on thesis. Requires --prediction-reason.")
    parser.add_argument("--predicted-by-date", help="BUY only, required with --play-type long -- ISO date "
                         "(YYYY-MM-DD) by which the position is predicted to be up. Mechanically enforced: "
                         "check_stops() resolves (does not necessarily force-sell) at this date.")
    parser.add_argument("--prediction-reason", help="BUY only, required with --play-type long or conviction -- "
                         "the specific evidence behind the thesis (cite fundamentals/congress/wiki narrative/"
                         "sentiment, not just optimism). Persisted to positions.prediction_reason.")
    parser.add_argument("--thesis-claim", help="BUY only -- the falsifiable directional claim ('X re-rates up "
                         "over N weeks because Y'), distinct from --prediction-reason's evidence citation. "
                         "Persisted to positions.thesis_claim and logged to position_thesis_log. Soft-required "
                         "for standard; hard-required for --play-type long/conviction (see --thesis-invalidation).")
    parser.add_argument("--thesis-invalidation", help="BUY only -- the specific, checkable condition that would "
                         "prove --thesis-claim wrong ('closes below 195', 'margin guidance cut'), not a vibe. "
                         "Persisted to positions.thesis_invalidation. Hard-required for --play-type long/"
                         "conviction (a conviction/long play held on an unfalsifiable thesis is just hoping); "
                         "soft-required for standard, matching --thesis's existing warn-only precedent.")
    parser.add_argument("--features", help="BUY only -- the same per-signal JSON passed to "
                         "record_decision.py decision ('{\"technical\": {\"direction\": \"bullish\", "
                         "\"confidence\": 0.6}, ...}'). Stored on the training_examples row written "
                         "automatically when the BUY fills, so signal-level attribution no longer "
                         "depends on a separate manual logging call being remembered.")
    parser.add_argument("--skip-guardrails", action="store_true", help="bypass guardrail checks (debug only)")

    args = parser.parse_args()

    if args.action == "status":
        account_data = get_account(args.account)
        positions = get_positions(args.account)
        equity = float(account_data.get("equity", 0))
        cash = float(account_data.get("cash", 0))
        peak_equity = _update_peak_equity(equity) if equity > 0 else _load_peak_equity()
        result = {
            "portfolio_value": equity,
            "peak_equity": peak_equity,
            "drawdown_pct": round((peak_equity - equity) / peak_equity * 100, 2) if peak_equity > 0 else 0.0,
            "cash": cash,
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

        # deployment_pressure tracks sustained under-deployment so research
        # effort can escalate (see deployment_pressure.research_escalation).
        # Zero extra API cost: cash/equity are already fetched above for
        # every status call. status is also called by the heartbeat outside
        # the 09:30-16:00 ET trading window (heartbeat runs until 23:00 ET)
        # -- only record_tick during real trading hours, or the streak
        # inflates with no corresponding trades. Off-hours calls just read
        # the existing state instead.
        sys.path.insert(0, str(WORKSPACE_DIR))
        import deployment_pressure
        cash_pct = (cash / equity * 100) if equity > 0 else 0.0
        params = load_params()
        threshold_pct = float(params.get("watchlist", {}).get("discovery_urgency", {}).get("cash_threshold_pct", 70.0))
        tick_interval_seconds = int(params.get("tick", {}).get("interval_seconds", deployment_pressure.DEFAULT_TICK_INTERVAL_SECONDS))
        if _is_regular_trading_hours():
            pressure_state = deployment_pressure.record_tick(cash_pct, threshold_pct, interval_seconds=tick_interval_seconds)
        else:
            pressure_state = deployment_pressure.read_state()
        result["deployment_pressure"] = {
            "cash_pct": round(cash_pct, 2),
            "consecutive_under_deployed_ticks": pressure_state["consecutive_under_deployed_ticks"],
            **deployment_pressure.research_escalation(pressure_state),
        }

        # gate_status: fresh-computed snapshot of every "count vs cap"
        # guardrail, folded into the always-called status action (same
        # "zero extra cost, computed from data already fetched here"
        # reasoning as deployment_pressure above) rather than a separate
        # subcommand the agent has to remember to call. Exists to eliminate
        # the LLM tick-agent's own stale-recall of these numbers across
        # ticks: confirmed live 2026-07-31, FLXS gated "Consumer Cyclical
        # FULL" for 29 consecutive ticks on a remembered 2/2 sector cap
        # while params.json's max_positions_per_sector had already been
        # raised to 5 hours earlier (journal/2026-07-31.md) -- the agent
        # never even reached the point of calling any execution path for a
        # candidate it had already self-excluded, so this has to be visible
        # in the one call that runs every tick regardless of what gets
        # decided next. Same root-cause class as the phantom "10 orders/day"
        # limit (real threshold is risk_guards.order_count_audit_threshold_daily,
        # and that gate is off via guardrail_gates.order_count_audit=false)
        # -- both were the agent treating a previously-derived number as
        # gospel instead of re-reading the source of truth. Nothing here is
        # a new gate or a new constraint -- read-only mirror of what
        # gate_sector_concentration/gate_daily_order_count/gate_max_positions
        # already compute on every guardrail check (same helpers, not a
        # re-derived copy, so this can't drift from the real gate math),
        # surfaced BEFORE the agent reasons about a trade instead of only
        # as a rejection message after one is attempted.
        risk_guards = params.get("risk_guards", {})
        gate_toggles = params.get("guardrail_gates", {})

        sector_counts: Dict[str, int] = {}
        try:
            conn = trader_db.get_conn()
            try:
                for row in trader_db.get_open_positions(conn):
                    sector = row.get("sector")
                    if sector:
                        sector_counts[sector] = sector_counts.get(sector, 0) + 1
            finally:
                conn.close()
        except Exception:
            sector_counts = {}

        max_per_sector = risk_guards.get("max_positions_per_sector")
        daily_count = _load_daily_order_count(_today_et({}))
        order_count_threshold = risk_guards.get("order_count_audit_threshold_daily")
        max_positions = params.get("risk", {}).get("max_positions")

        result["gate_status"] = {
            "sector_concentration": {
                "gate_mode": gate_toggles.get("sector_concentration", True),
                "cap_per_sector": max_per_sector,
                "by_sector": {
                    sector: {
                        "open": count, "cap": max_per_sector,
                        "at_cap": max_per_sector is not None and count >= max_per_sector,
                    }
                    for sector, count in sector_counts.items()
                },
            },
            "daily_order_count": {
                "gate_enabled": gate_toggles.get("order_count_audit", True) is not False,
                "count_today": daily_count,
                "threshold": order_count_threshold,
                "at_threshold": order_count_threshold is not None and daily_count >= order_count_threshold,
            },
            "max_positions": {
                "gate_enabled": gate_toggles.get("max_positions", True) is not False,
                "open_count": len(positions),
                "cap": max_positions,
            },
        }

        print(json.dumps(result, indent=2))
        return

    if args.action == "check-stops":
        breaches = check_stops(args.account)
        # The in-tick scan and the resting broker-side floor are two halves
        # of the same mechanism: the scan is tighter and dynamic but only
        # runs when a tick runs, the resting stop is static but survives a
        # tick that never ran. Reconciling here (rather than inside
        # check_stops()) keeps the scan itself side-effect-free while still
        # giving the floor a once-per-tick chance to be repaired.
        print(json.dumps({
            "breaches": breaches,
            "stopped_out": reconcile_stopped_out_positions(args.account),
            "protective_stops": reconcile_protective_stops(args.account),
        }, indent=2))
        return

    if args.action == "sync-stops":
        # Explicit pre-session GTC audit (strategy.md's "clear all
        # stale/unfilled GTC orders before first tick"), plus closing the
        # books on anything a broker-side stop exited overnight and placing
        # the protective stop any position is missing.
        print(json.dumps({
            "stopped_out": reconcile_stopped_out_positions(args.account),
            "protective_stops": reconcile_protective_stops(args.account),
        }, indent=2))
        return

    if not args.ticker or not args.qty:
        print(json.dumps({"error": "ticker and qty required for BUY/SELL"}))
        sys.exit(1)

    if args.play_type == "long":
        if args.action != "BUY":
            print(json.dumps({"error": "--play-type long only valid for BUY"}))
            sys.exit(1)
        if not args.predicted_by_date or not args.prediction_reason:
            print(json.dumps({
                "error": "--play-type long requires both --predicted-by-date and --prediction-reason "
                         "(a long play needs an explicit deadline and a stated, evidence-backed reason -- "
                         "not open-ended patience, see params.json risk.long_play)",
            }))
            sys.exit(1)
        try:
            datetime.strptime(args.predicted_by_date, "%Y-%m-%d")
        except ValueError:
            print(json.dumps({"error": f"--predicted-by-date must be YYYY-MM-DD, got {args.predicted_by_date!r}"}))
            sys.exit(1)
        if not args.thesis_invalidation:
            print(json.dumps({
                "error": "--play-type long requires --thesis-invalidation "
                         "(the specific, checkable condition that would prove this wrong -- a long play "
                         "held on an unfalsifiable thesis is just hoping)",
            }))
            sys.exit(1)

    if args.play_type == "conviction":
        if args.action != "BUY":
            print(json.dumps({"error": "--play-type conviction only valid for BUY"}))
            sys.exit(1)
        if not args.prediction_reason:
            print(json.dumps({
                "error": "--play-type conviction requires --prediction-reason "
                         "(a stated, evidence-backed thesis -- see params.json risk.conviction_play)",
            }))
            sys.exit(1)
        if not args.thesis_invalidation:
            print(json.dumps({
                "error": "--play-type conviction requires --thesis-invalidation "
                         "(the specific, checkable condition that would prove this wrong -- a conviction play "
                         "held on an unfalsifiable thesis is just hoping)",
            }))
            sys.exit(1)

    # Parsed before anything is submitted: a malformed --features blob is a
    # typo to fix, not a reason to discover the problem after real shares
    # have changed hands.
    entry_features: Dict[str, Any] = {}
    if args.features:
        try:
            entry_features = json.loads(args.features)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"--features not valid JSON: {e}"}))
            sys.exit(1)
        if not isinstance(entry_features, dict):
            print(json.dumps({"error": "--features must be a JSON object"}))
            sys.exit(1)

    # Locked from the guardrail check through order submission — closes the
    # cross-process TOCTOU race gate_order_idempotency alone couldn't (see
    # _order_lock's docstring). Everything after place_order() returns
    # (bookkeeping, DB writes) doesn't touch Alpaca's shared order-book
    # state, so it doesn't need to stay inside the lock.
    with _order_lock(args.ticker):
        if not args.skip_guardrails:
            granted, reason, gate_results = check_order(
                args.account, args.action, args.ticker, args.qty,
                price=args.price, conviction=args.conviction, sector=args.sector,
                play_type=args.play_type,
            )
            if not granted:
                print(json.dumps({"error": f"guardrail: {reason}", "gates": gate_results}, indent=2))
                sys.exit(1)

        # Capture entry price + pre-sell share count BEFORE selling — position
        # may be gone from get_positions() afterward (full close), and this is
        # what lets a SELL feed a real win/loss back into bankroll.py's
        # adaptive ceiling, and tells positions-table bookkeeping below whether
        # this was a full exit or a trim.
        entry_price = None
        pre_sell_qty = None
        cancelled_stops: List[str] = []
        if args.action == "SELL":
            for p in get_positions(args.account):
                if p["symbol"].upper() == args.ticker.upper():
                    entry_price = float(p["avg_entry_price"])
                    pre_sell_qty = float(p["qty"])
                    break

            if entry_price is None:
                # Live Alpaca lookup missed this ticker (stale broker sync, a
                # race right at fill time, or the position already closed
                # elsewhere) -- fall back to the local trader_db positions
                # row before giving up entirely. Only trust it if still
                # 'open': a closed/stale local row would be worse than no
                # data. Never fabricate entry_price = exit_price as a last
                # resort -- that would record a false win/loss and corrupt
                # bankroll/experience/training data, a worse failure mode
                # than skipping the outcome bookkeeping below.
                try:
                    conn = trader_db.get_conn()
                    try:
                        fallback_row = trader_db.get_position(conn, args.ticker.upper())
                    finally:
                        conn.close()
                except Exception:
                    fallback_row = None
                if fallback_row and fallback_row.get("status") == "open":
                    if fallback_row.get("entry_price") is not None:
                        entry_price = float(fallback_row["entry_price"])
                    if pre_sell_qty is None and fallback_row.get("shares") is not None:
                        pre_sell_qty = float(fallback_row["shares"])

            if entry_price is None:
                # Previously failed silently here: close_trade_outcome()
                # below never ran (its call is gated on entry_price is not
                # None), which skipped the bankroll update, experience.json
                # win/loss bump, and decisions/training_examples outcome
                # label for a real, already-executed SELL -- with zero
                # visibility. Confirmed live: 27 of 61 total_trades sit
                # "unclassified". Loud now instead of silent; the skip
                # itself is still correct (no real entry_price to compute
                # pnl from), only the visibility changes.
                print(json.dumps({
                    "warning": f"entry_price unavailable for SELL {args.ticker.upper()} "
                               f"(missing from both live Alpaca positions and local trader_db) -- "
                               f"outcome bookkeeping (bankroll/experience/decisions/training_examples) "
                               f"skipped for this trade",
                }), file=sys.stderr)

            # MUST happen before the SELL is submitted: a resting GTC sell
            # stop reserves the shares it covers, so Alpaca rejects a market
            # SELL of the same position for insufficient quantity while that
            # order is still on the book. This is also the "no duplicate /
            # conflicting exits on one position" cleanup — the trailing-stop
            # exit and the broker-side floor must never both be live.
            cancelled_stops = cancel_protective_stops(args.account, args.ticker)

        side = args.action.lower()
        order = place_order(args.account, args.ticker, args.qty, side)
    print(json.dumps(order, indent=2))
    record_order_submitted(args.ticker, args.action)

    if cancelled_stops:
        print(json.dumps({"cancelled_protective_stops": cancelled_stops}), file=sys.stderr)

    if args.action == "SELL" and entry_price is not None:
        # Read before close_position() flips status: this is the correlation
        # key that tells decisions.record_trade_close WHICH training_examples
        # row belongs to the position being closed (see close_trade_outcome).
        position_entry_time = None
        try:
            conn = trader_db.get_conn()
            try:
                pos_row = trader_db.get_position(conn, args.ticker.upper())
            finally:
                conn.close()
            if pos_row and pos_row.get("status") == "open":
                position_entry_time = pos_row.get("entry_time")
        except Exception:
            position_entry_time = None  # falls back to newest-open-entry-row matching

        # 2026-08-03: --price is optional and, unlike the BUY path, this used
        # to fall straight back to entry_price when omitted -- silently
        # computing exit_price == entry_price -> pnl == $0.00, mislabeling a
        # real profitable exit as a LOSS (live incident: ZBRA/DXCM/OOMA
        # bootstrap quick-exits on 2026-08-03). Now mirrors the BUY path's
        # wait_for_fill() real-fill lookup when --price wasn't supplied, and
        # only truly falls back to entry_price (loudly) if that also fails.
        sell_fill_price = None
        if args.price is None and order.get("id"):
            filled_sell_order = wait_for_fill(args.account, order.get("id"))
            if filled_sell_order and filled_sell_order.get("filled_avg_price"):
                try:
                    sell_fill_price = float(filled_sell_order["filled_avg_price"])
                except (TypeError, ValueError):
                    sell_fill_price = None

        if args.price is not None:
            exit_price = args.price
        elif sell_fill_price is not None:
            exit_price = sell_fill_price
        else:
            exit_price = entry_price
            print(json.dumps({
                "warning": f"exit_price unavailable for SELL {args.ticker.upper()} "
                           f"(--price not passed and no fill price from wait_for_fill) -- "
                           f"falling back to entry_price, realized_pnl for this trade will be $0.00 "
                           f"and does not reflect the real exit",
            }), file=sys.stderr)

        outcome = close_trade_outcome(args.account, args.ticker, entry_price, exit_price, args.qty,
                                       position_entry_time=position_entry_time)
        if outcome["outcome_label_warning"]:
            print(json.dumps({"outcome_label_warning": outcome["outcome_label_warning"]}), file=sys.stderr)

        # Note: decisions.record_decision() also inserts a fresh "exit"-type
        # training_examples row here (example_type != ENTRY, so it can't
        # merge into the BUY's row) -- this stays permanently unlabeled
        # (nothing calls label_training_example on it; record_trade_close
        # above already labeled the real ENTRY row) and so is correctly
        # excluded from signal_scorecard.py's fetch_labeled_training_examples
        # (WHERE label_win IS NOT NULL). Verified no double-counting risk,
        # just an extra inert row -- checked before shipping this change.
        _record_decision_row("SELL", args.ticker.upper(), args.conviction, args.close_reason, entry_features)

        # positions table bookkeeping -- best-effort, never blocks the
        # trade (the order already executed by the time this runs, same
        # fail-open philosophy as close_trade_outcome's Postgres/experience
        # side already documented above).
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            conn = trader_db.get_conn()
            try:
                remaining = (pre_sell_qty or 0) - args.qty
                if remaining <= 0:
                    trader_db.close_position(
                        conn, ticker=args.ticker, closed_at=now_iso,
                        close_reason=args.close_reason or "manual SELL",
                        realized_pnl=outcome["pnl"], realized_return_pct=outcome["return_pct"],
                    )
                else:
                    trader_db.upsert_position(
                        conn, ticker=args.ticker, shares=remaining, entry_price=entry_price, entry_time=now_iso,
                    )
            finally:
                conn.close()
        except Exception as e:
            print(json.dumps({"warning": f"positions table write failed: {e}"}), file=sys.stderr)

        # A partial exit leaves shares still exposed with no resting stop
        # (the old one covered the pre-trim size and was cancelled above) --
        # re-place it now rather than waiting for the next tick's
        # reconcile, which may not run.
        if (pre_sell_qty or 0) - args.qty > 0:
            stop_result = ensure_protective_stop(args.account, args.ticker)
            print(json.dumps({"protective_stop": stop_result}), file=sys.stderr)

    if args.action == "BUY" and args.price is not None:
        # bankroll.py's total_deployed was write-only until 2026-07-27 --
        # nothing ever called into it on the BUY side. See bankroll.py's
        # record_deployment().
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import bankroll
        bankroll_state = bankroll.read_bankroll()
        bankroll.record_deployment(bankroll_state, args.qty * args.price)
        bankroll.write_bankroll(bankroll_state)

        # positions table bookkeeping -- best-effort, never blocks the
        # trade. shares comes from a fresh get_positions() call (Alpaca's
        # own post-fill total) rather than adding args.qty to whatever we
        # think was held before, so a scale-in's stored share count can
        # never drift from reality.
        if not args.thesis:
            print(json.dumps({"warning": "BUY executed with no --thesis provided"}), file=sys.stderr)
        if not args.thesis_claim or not args.thesis_invalidation:
            print(json.dumps({
                "warning": "BUY executed with no --thesis-claim/--thesis-invalidation -- "
                           "position won't have a falsifiable thesis to re-check later",
            }), file=sys.stderr)
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            total_shares = args.qty
            for p in get_positions(args.account):
                if p["symbol"].upper() == args.ticker.upper():
                    total_shares = float(p["qty"])
                    break
            conn = trader_db.get_conn()
            try:
                existing = trader_db.get_position(conn, args.ticker.upper())
                is_scale_in = bool(existing and existing.get("status") == "open")
                trader_db.upsert_position(
                    conn, ticker=args.ticker, shares=total_shares, entry_price=args.price,
                    entry_time=now_iso, sector=args.sector, thesis=args.thesis,
                    play_type=args.play_type, predicted_by_date=args.predicted_by_date,
                    prediction_reason=args.prediction_reason,
                    thesis_claim=args.thesis_claim, thesis_invalidation=args.thesis_invalidation,
                    thesis_entry_signals=args.features,
                )
                if args.thesis_claim or args.thesis_invalidation:
                    trader_db.log_thesis_event(
                        conn, ticker=args.ticker, event_type="scale_in" if is_scale_in else "entry",
                        claim=args.thesis_claim, invalidation=args.thesis_invalidation,
                        signals_snapshot=args.features, now=now_iso,
                    )
            finally:
                conn.close()
        except Exception as e:
            print(json.dumps({"warning": f"positions table write failed: {e}"}), file=sys.stderr)

        # A real BUY is evidence deployment pressure eased -- reset the
        # consecutive-under-deployed streak so the next status call
        # recomputes cash_pct fresh rather than staying artificially low.
        import deployment_pressure
        deployment_pressure.reset()

    if args.action == "BUY":
        # Deliberately outside the `args.price is not None` block above: the
        # broker-side stop and the training row must be written for EVERY
        # BUY, not only the ones that happened to pass a price.
        filled_order = wait_for_fill(args.account, order.get("id")) if order.get("id") else None
        filled_status = str((filled_order or order).get("status", "")).lower()

        # ── Broker-side protective stop ──────────────────────────────────
        # The hard floor that survives a tick timeout or a gateway restart
        # (see place_stop_order). Sized against the position Alpaca reports
        # after the fill, so a scale-in ends up with ONE stop covering the
        # whole position rather than one per BUY, and an order that hasn't
        # filled yet simply reports no_position and gets its stop from the
        # next check-stops reconcile instead.
        if _protective_stops_enabled():
            print(json.dumps({"protective_stop": ensure_protective_stop(args.account, args.ticker)}),
                   file=sys.stderr)

        # ── Training-example row ─────────────────────────────────────────
        # Written here in code, on every fill. Previously this depended on
        # the agent remembering a separate `record_decision.py decision`
        # call, which gets skipped when a tick runs short on time -- only
        # ~45% of executed trades ever got a row, and the self-improvement
        # loop can't learn from trades it has no record of. A later
        # record_decision.py call for the same position merges into THIS
        # row (decisions.record_decision) instead of creating a second one.
        try:
            import decisions
            entry_time = None
            try:
                conn = trader_db.get_conn()
                try:
                    pos_row = trader_db.get_position(conn, args.ticker.upper())
                finally:
                    conn.close()
                if pos_row and pos_row.get("status") == "open":
                    entry_time = pos_row.get("entry_time")
            except Exception:
                entry_time = None

            fill_price = None
            if filled_order and filled_order.get("filled_avg_price"):
                try:
                    fill_price = float(filled_order["filled_avg_price"])
                except (TypeError, ValueError):
                    fill_price = None

            features = dict(entry_features)
            # Non-signal context block: ignored by signal_scorecard.py's
            # per-signal tally (not {direction, confidence}-shaped), kept so
            # a row is still self-describing when --features wasn't passed.
            features.setdefault("entry", {
                "price": fill_price if fill_price is not None else args.price,
                "qty": args.qty,
                "conviction": args.conviction,
                "sector": args.sector,
                "play_type": args.play_type,
                "predicted_by_date": args.predicted_by_date,
                "prediction_reason": args.prediction_reason,
                "order_id": order.get("id"),
                "fill_status": filled_status or None,
            })
            entry_result = decisions.record_entry_example(
                ticker=args.ticker.upper(), features=features, position_entry_time=entry_time,
            )
            if entry_result.get("error") or not entry_features:
                print(json.dumps({"training_example": entry_result,
                                   "note": None if entry_features else
                                   "no --features passed: row has entry metadata but no scored signals"}),
                       file=sys.stderr)
        except Exception as e:
            print(json.dumps({"warning": f"training_example write failed: {e}"}), file=sys.stderr)

        _record_decision_row("BUY", args.ticker.upper(), args.conviction, args.thesis, features)


if __name__ == "__main__":
    main()
