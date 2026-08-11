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

import alpaca_client
from alpaca_client import (  # noqa: F401 -- re-exported for existing callers
    WORKSPACE_DIR, PARAMS_PATH, STATE_DIR, STOPS_STATE_PATH, RECENT_ORDERS_PATH,
    DAILY_ORDER_COUNT_PATH, PEAK_EQUITY_PATH, EXPERIENCE_PATH, DEFAULT_STOP_LOSS_PCT,
    ALPACA_BASE_URL, get_headers, get_account, get_positions, get_open_orders,
    place_order, get_order, cancel_order, get_closed_orders, place_stop_order,
    load_params,
)
_get_alpaca_base_url = alpaca_client._get_alpaca_base_url
_get_keys = alpaca_client._get_keys
_record_alpaca_call = alpaca_client._record_alpaca_call


# ─────────────────────────────────────────────────────────────────────────────
# Broker-side protective stops — now scripts/protective_stops.py.
# ─────────────────────────────────────────────────────────────────────────────

import protective_stops
from protective_stops import (  # noqa: F401 -- re-exported for existing callers
    PROTECTIVE_STOP_ORDER_TYPES, INDEX_ANCHOR_TICKERS, _order_type_of, is_protective_stop_order,
    _round_stop_price, protective_stop_price, _protective_stops_enabled, open_protective_stops,
    cancel_protective_stops, _wait_orders_cleared, wait_for_fill, _held_position,
    ensure_protective_stop, reconcile_protective_stops, reconcile_stopped_out_positions,
)



# ─────────────────────────────────────────────────────────────────────────────
# Guardrail gates — now scripts/guardrail_gates.py. Re-exported below so
# external callers (replay_order.py, discovery_daemon.py, tests) and this
# file's own remaining functions (protective stops, check_stops, CLI) keep
# working unqualified against executor.py's own module globals.
# ─────────────────────────────────────────────────────────────────────────────

import guardrail_gates
from guardrail_gates import (  # noqa: F401 -- re-exported for existing callers
    GATES, ORDER_LOCK_DIR, gate_cash, _size_cap_pct_for, gate_position_size, gate_long_play,
    gate_conviction_play, gate_max_portfolio_risk, gate_max_positions, _sector_of,
    gate_sector_concentration, gate_catalyst_liquidity, gate_hours, _is_regular_trading_hours,
    gate_bankroll, gate_conviction, _load_recent_orders, _load_experience, _save_experience,
    record_experience_trade, record_experience_outcome, record_order_submitted,
    gate_duplicate_order, gate_order_idempotency, _today_et, _load_daily_order_count,
    _record_daily_order, gate_daily_order_count, _load_peak_equity, _update_peak_equity,
    gate_drawdown_circuit_breaker, _order_lock, run_gates, check_order,
)


# ─────────────────────────────────────────────────────────────────────────────
# Stop-loss / trailing-stop scanning — now scripts/stop_scanner.py.
# ─────────────────────────────────────────────────────────────────────────────

import stop_scanner
from stop_scanner import (  # noqa: F401 -- re-exported for existing callers
    _load_stop_state, _save_stop_state, _fetch_vol_20d, check_stops,
)


# ─────────────────────────────────────────────────────────────────────────────
# Post-SELL bookkeeping — now scripts/trade_bookkeeping.py.
# ─────────────────────────────────────────────────────────────────────────────

import trade_bookkeeping
from trade_bookkeeping import close_trade_outcome, _record_decision_row  # noqa: F401


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
    parser.add_argument("--market-cap", type=float, help="BUY only -- used by the catalyst-liquidity gate "
                         "(v1.18). Optional/fail-open: only blocks when both this and --avg-dollar-volume "
                         "are passed and the name is sub-$500M with too-thin volume.")
    parser.add_argument("--avg-dollar-volume", type=float, help="BUY only -- avg daily dollar volume, used "
                         "by the catalyst-liquidity gate alongside --market-cap.")
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
                play_type=args.play_type, market_cap=args.market_cap,
                avg_dollar_volume=args.avg_dollar_volume,
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
        elif args.action == "BUY":
            # Same requirement as the SELL path above, opposite direction:
            # Alpaca 403s a new BUY on a symbol that already carries a
            # resting protective (sell) stop -- confirmed live 2026-08-05,
            # blocked every BFST/BBSI scale-in attempt for the session.
            # Cancel it first; ensure_protective_stop() below (existing,
            # unconditional BUY-path call) re-places it sized to the whole
            # position after the fill, so the floor is never actually gone,
            # just briefly absent around this one order.
            if open_protective_stops(args.account, args.ticker):
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
        #
        # 2026-08-10: that fix has a gap -- wait_for_fill()'s 1.0s default
        # timeout (2-3 polls) is fine for the BUY path, where a slow fill
        # just means slightly-stale training-example features, but here a
        # miss doesn't just lose a feature, it silently corrupts the
        # permanent realized_pnl/realized_return_pct record with no
        # after-the-fact reconciliation for closed trades (unlike open
        # positions, which reconcile_positions.py now actually catches).
        # Confirmed live: VSXY's real +10.80% profit-target exit recorded
        # $0.00/0.00% this way. A few extra seconds here is trivial against
        # a 300s tick interval; use a longer, dedicated timeout instead of
        # the shared BUY-path default.
        sell_fill_price = None
        if args.price is None and order.get("id"):
            filled_sell_order = wait_for_fill(args.account, order.get("id"), timeout=5.0)
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
        if outcome["zero_pnl_anomaly"]:
            print(json.dumps({
                "ZERO_PNL_ANOMALY": f"{args.ticker.upper()} closed with realized_pnl == $0.00 exactly "
                                     f"(entry={entry_price}, exit={exit_price}) -- near-impossible by "
                                     f"chance, check for exit_price silently defaulting to entry_price",
            }), file=sys.stderr)

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
