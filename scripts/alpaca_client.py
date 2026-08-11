#!/usr/bin/env python3
"""Alpaca REST client — the low-level HTTP layer (account/positions/orders)
plus the workspace's shared filesystem-layout constants. Extracted
2026-08-11 from executor.py (was one 2,562-line file) as the base module
everything else imports; has no dependency on any other extracted module.
executor.py re-exports these names so existing callers (discovery_daemon.py,
position_sizing.py, etc.) don't need to change."""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import db_writer

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
PARAMS_PATH = WORKSPACE_DIR / "params.json"
STATE_DIR = WORKSPACE_DIR / "state"
STOPS_STATE_PATH = STATE_DIR / "guardrail_stops.json"
RECENT_ORDERS_PATH = STATE_DIR / "recent_orders.json"
DAILY_ORDER_COUNT_PATH = STATE_DIR / "daily_order_count.json"
PEAK_EQUITY_PATH = STATE_DIR / "peak_equity.json"
EXPERIENCE_PATH = WORKSPACE_DIR / "experience.json"

_DEFAULT_ALPACA_BASE_URL = "https://paper-api.alpaca.markets"

# Last-resort fallback if params.json loads fine but risk.stop_loss_pct is
# missing (bad merge, manual edit, future refactor -- not "file absent",
# load_params() already raises on that). Intentionally mirrors the current
# live params.json value, not some other historical value: a wrong-but-tight
# stop still protects capital, a wrong-but-loose one doesn't.
DEFAULT_STOP_LOSS_PCT = -6.0


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

