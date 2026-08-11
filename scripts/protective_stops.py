#!/usr/bin/env python3
"""Broker-side protective stops — the static worst-case backstop that
survives a tick timeout or a gateway restart. check_stops()'s ratcheting
trailing stop still runs on top of this and is normally tighter/dynamic —
this is the floor underneath it, not a replacement for it.

Everything here is best-effort: a protective-stop failure must never look
like a failed trade (the real order has already executed by the time any
of it runs), same fail-open philosophy as trade_bookkeeping.close_trade_outcome().

Extracted 2026-08-11 from executor.py, last of the five extractions since
it depends on all three others: alpaca_client.py (the Alpaca REST calls),
guardrail_gates.py (record_order_submitted), and trade_bookkeeping.py
(close_trade_outcome) -- reconcile_stopped_out_positions() below is the
one function in this module needing the latter two.
"""
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import alpaca_client
import guardrail_gates
import trade_bookkeeping
import trader_db

PROTECTIVE_STOP_ORDER_TYPES = ("stop", "stop_limit", "trailing_stop")

# 2026-08-10 (Raf's direction): index-anchor positions are exempt from the
# oversized-position trim on the upside -- see decision_heuristics.md's
# index_anchor_entry_v1 node, "Sizing guidance." Mirrors that node's own
# eligible-instrument whitelist (broad, mega-cap-liquid, non-leveraged index
# ETFs only) rather than a new DB column, since that whitelist is already
# the canonical definition of "is this ticker an index anchor" used
# everywhere else.
INDEX_ANCHOR_TICKERS = frozenset({"SPY", "QQQ", "DIA", "IWM"})


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
    params = params if params is not None else alpaca_client.load_params()
    stop_loss_pct = abs(float(params.get("risk", {}).get("stop_loss_pct", alpaca_client.DEFAULT_STOP_LOSS_PCT)))
    return _round_stop_price(entry_price * (1 - stop_loss_pct / 100.0))


def _protective_stops_enabled(params: Optional[Dict[str, Any]] = None) -> bool:
    """params.json guardrail_gates.broker_stop_order, default on — same
    convention as every other gate toggle (missing == enabled)."""
    try:
        params = params if params is not None else alpaca_client.load_params()
        return params.get("guardrail_gates", {}).get("broker_stop_order", True) is not False
    except (OSError, ValueError):
        return True


def open_protective_stops(account: str, ticker: Optional[str] = None) -> List[Dict[str, Any]]:
    """Resting sell-stop orders at Alpaca, optionally for one ticker.
    Returns [] on any API error (never raises) — callers treat "can't tell"
    as "nothing to clean up" and move on."""
    try:
        orders = alpaca_client.get_open_orders(account, ticker)
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
            alpaca_client.cancel_order(account, order_id)
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
            still_open = {str(o.get("id")) for o in alpaca_client.get_open_orders(account, ticker)}
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
            order = alpaca_client.get_order(account, order_id)
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
        for p in alpaca_client.get_positions(account):
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
        order = alpaca_client.place_stop_order(account, ticker, qty, stop_price)
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
        positions = alpaca_client.get_positions(account)
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
    trade_bookkeeping.close_trade_outcome() + close_position() bookkeeping a normal SELL
    does, using the broker's own fill price.

    Only touches positions the local DB believes are open and Alpaca no
    longer holds, and close_position() flips status on the way out, so it
    can't process the same exit twice. Never raises.
    """
    results: List[Dict[str, Any]] = []
    try:
        if positions is None:
            positions = alpaca_client.get_positions(account)
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
            orders = alpaca_client.get_closed_orders(account, ticker)
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

            outcome = trade_bookkeeping.close_trade_outcome(account, ticker, entry_price, exit_price, qty,
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
            guardrail_gates.record_order_submitted(ticker, "SELL")
            results.append({"ticker": ticker, "status": "closed_from_broker_fill",
                             "exit_price": exit_price, "qty": qty,
                             "pnl": round(outcome["pnl"], 2),
                             "return_pct": round(outcome["return_pct"], 2),
                             "outcome_label_warning": outcome["outcome_label_warning"],
                             "zero_pnl_anomaly": outcome["zero_pnl_anomaly"]})
        except Exception as e:
            results.append({"ticker": ticker, "status": "error", "error": str(e)})
    return results
