#!/usr/bin/env python3
"""
replay_order.py — simulation-only order/gate path for the historical-
replay harness (2026-08-01).

A deliberately SEPARATE file from executor.py, not a --simulated flag
bolted onto it -- this keeps live-trading code textually unchanged past
executor.py's run_gates() extraction (a structural guarantee, not just a
runtime flag, that a bug here can't leak into a real order). This script
never imports urllib.request and never talks to Alpaca in any way.

Given a --session-id, resolves that backtest session's own isolated
SQLite file (scripts/backtest_session.py), builds a simulated portfolio
context from it, runs the trade through the exact same GATES chain live
trading uses (executor.run_gates), and on grant simulates an immediate
fill at the given price -- writing positions/decisions/training_examples
rows via the SAME trader_db.py/decisions.py functions live trading uses,
just pointed at the session's DB file instead of state/trader.db.

Gates that only make sense for live, concurrent-process trading
(order_idempotency, the cross-process concern in duplicate_order) or that
read live-only state (bankroll's real bankroll.json ceiling) are force-
disabled for every call here via an explicit toggles override -- see
_backtest_toggles(). Everything else runs unmodified.

Usage:
    python3 scripts/replay_order.py --session-id s1 --action BUY --ticker SOFI \
        --qty 5 --price 4.00 --conviction 0.8 --sector Financial --thesis "..." \
        [--play-type standard|long|conviction] [--predicted-by-date ...] \
        [--prediction-reason ...] [--features '{"macdh": {...}}']

    python3 scripts/replay_order.py --session-id s1 --action SELL --ticker SOFI \
        --qty 5 --price 4.20 --close-reason "profit target"
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_session  # noqa: E402
import decisions  # noqa: E402
import executor  # noqa: E402
import trader_db  # noqa: E402

# Gates disabled for every backtest-session order, regardless of what
# params.json's live guardrail_gates say. Every one of these reads AND
# WRITES a fixed-path live state file (not the context dict passed in) --
# confirmed live during manual testing: gate_drawdown_circuit_breaker
# silently wrote to the real state/peak_equity.json on a plain backtest
# BUY call (harmless only because the test session's $10k portfolio_value
# happened to be below the real ~$10,426 peak -- a higher backtest equity
# would have corrupted live peak tracking). The "everything else needs no
# changes beyond a correctly-shaped context" assumption from the original
# plan was wrong for these three specifically; audited the rest by hand
# (cash/position_size/long_play/conviction_play/max_portfolio_risk/
# max_positions/sector_concentration/conviction all read ONLY context/
# trade_action/params.json, no live state files).
#   order_idempotency, duplicate_order (cross-process race): guard against
#     two LIVE processes racing to double-buy the same ticker (see their
#     docstrings -- the IP/KRC/BFST incidents). Reads state/recent_orders.json.
#     A replay session is single-threaded; meaningless here, and
#     duplicate_order in particular could otherwise reject a deliberate
#     same-tick scale-in.
#   order_count_audit: reads/writes state/daily_order_count.json, a
#     shared daily counter keyed by real wall-clock date -- meaningless
#     (and actively wrong) against a simulated historical date.
#   drawdown_circuit_breaker: reads/writes state/peak_equity.json, the
#     REAL live portfolio's peak -- see the corruption risk above.
#   bankroll: reads the real bankroll.json ceiling. A backtest session
#     must never perturb (or be perturbed by) live capital calibration --
#     sizing in backtest mode is governed by max_position_pct/play-type
#     caps alone, same as every other gate.
BACKTEST_DISABLED_GATES = [
    "order_idempotency", "duplicate_order", "order_count_audit",
    "drawdown_circuit_breaker", "bankroll",
]


def _backtest_toggles() -> dict:
    live_toggles = dict(executor.load_params().get("guardrail_gates", {}))
    for gate in BACKTEST_DISABLED_GATES:
        live_toggles[gate] = False
    return live_toggles


def _simulate_buy(session_id: str, db_path: Path, ticker: str, qty: int, price: float,
                   sim_timestamp: str, conviction, sector, thesis, play_type, predicted_by_date,
                   prediction_reason, features: dict, thesis_claim=None, thesis_invalidation=None) -> dict:
    conn = trader_db.get_conn(db_path)
    try:
        existing = trader_db.get_position(conn, ticker)
        is_scale_in = bool(existing and existing.get("status") == "open")
        if existing and existing.get("status") == "open":
            # Scale-in: weighted-average entry price, matching the economics
            # of buying more shares at a different price. Live executor.py
            # gets this from Alpaca's own post-fill total; there's no broker
            # here, so it's computed directly from the two fills.
            old_shares, old_entry = existing["shares"], existing["entry_price"]
            total_shares = old_shares + qty
            entry_price = ((old_shares * old_entry) + (qty * price)) / total_shares
            entry_time = existing["entry_time"]  # scale-ins don't reset entry_time
        else:
            total_shares = qty
            entry_price = price
            entry_time = sim_timestamp

        thesis_entry_signals = json.dumps(features) if features else None
        trader_db.upsert_position(
            conn, ticker=ticker, shares=total_shares, entry_price=entry_price,
            entry_time=entry_time, sector=sector, thesis=thesis, play_type=play_type,
            predicted_by_date=predicted_by_date, prediction_reason=prediction_reason,
            thesis_claim=thesis_claim, thesis_invalidation=thesis_invalidation,
            thesis_entry_signals=thesis_entry_signals,
        )
        if thesis_claim or thesis_invalidation:
            trader_db.log_thesis_event(
                conn, ticker=ticker, event_type="scale_in" if is_scale_in else "entry",
                claim=thesis_claim, invalidation=thesis_invalidation,
                signals_snapshot=thesis_entry_signals, now=sim_timestamp,
            )
        # upsert_position()'s ON CONFLICT clause deliberately does NOT
        # update entry_price on a scale-in (live trading treats Alpaca's
        # own avg_entry_price as the source of truth, not this field --
        # see the positions table's schema comment). A backtest session has
        # no broker, so this field IS the only source of truth -- without
        # this explicit UPDATE, a scale-in's weighted-average price silently
        # never persists and every later gate/PnL calc for this position
        # uses the stale original entry price. Confirmed by hand: a 5sh@$4.00
        # + 3sh@$4.20 scale-in produced pnl/return_pct computed off entry
        # price 4.00 instead of the correct weighted 4.075 before this fix.
        with conn:
            conn.execute("UPDATE positions SET entry_price = ? WHERE ticker = ?", (entry_price, ticker))
    finally:
        conn.close()

    decisions.record_decision(
        trader_id="stonks-backtest", ticker=ticker, action="BUY", rationale=thesis or "",
        conviction=conviction or 0.0, features=features, db_path=db_path,
        position_entry_time=entry_time,
    )
    entry_features = dict(features)
    entry_features.setdefault("entry", {
        "price": price, "qty": qty, "conviction": conviction, "sector": sector,
        "play_type": play_type, "predicted_by_date": predicted_by_date,
        "prediction_reason": prediction_reason, "session_id": session_id, "simulated": True,
    })
    decisions.record_entry_example(
        ticker=ticker, features=entry_features, position_entry_time=entry_time, db_path=db_path,
    )
    return {
        "simulated_fill": {"ticker": ticker, "side": "buy", "qty": qty, "price": price,
                             "total_shares": total_shares, "entry_price": entry_price},
    }


def _simulate_sell(session_id: str, db_path: Path, ticker: str, qty: int, price: float,
                    sim_timestamp: str, close_reason: str) -> dict:
    conn = trader_db.get_conn(db_path)
    try:
        existing = trader_db.get_position(conn, ticker)
        if not existing or existing.get("status") != "open":
            return {"error": f"no open backtest position for {ticker!r} in session {session_id!r}"}
        entry_price = existing["entry_price"]
        entry_time = existing["entry_time"]
        pre_sell_qty = existing["shares"]
        remaining = pre_sell_qty - qty

        pnl = (price - entry_price) * qty
        return_pct = (price - entry_price) / entry_price * 100 if entry_price else 0.0

        if remaining <= 0:
            trader_db.close_position(
                conn, ticker=ticker, closed_at=sim_timestamp, close_reason=close_reason or "SELL",
                realized_pnl=pnl, realized_return_pct=return_pct,
            )
        else:
            trader_db.upsert_position(
                conn, ticker=ticker, shares=remaining, entry_price=entry_price, entry_time=entry_time,
            )
    finally:
        conn.close()

    # Order matters: record_trade_close() MUST run before record_decision()
    # below. find_entry_training_example()'s exact-position-link tier
    # matches on position_entry_time alone (WHERE ... position_entry_time = ?),
    # not example_type='entry' -- if the SELL's own decision row were
    # written first with the same position_entry_time, it would be
    # label_win-IS-NULL and newest-by-created_at, so record_trade_close
    # would label THAT row instead of the real entry row it's meant to
    # label. This is the exact mislabeling bug fixed live this morning,
    # reintroduced by ordering rather than by the correlation logic itself
    # -- confirmed by hand: reversing this order labeled the SELL's own
    # 'exit'-type row (features={}) instead of the 'entry' row that
    # actually carries the predictive signals.
    close_result = decisions.record_trade_close(
        trader_id="stonks-backtest", ticker=ticker, trade_id=None, pnl=pnl, return_pct=return_pct,
        db_path=db_path, position_entry_time=entry_time,
    )
    decisions.record_decision(
        trader_id="stonks-backtest", ticker=ticker, action="SELL",
        rationale=close_reason or "", db_path=db_path, position_entry_time=entry_time,
    )
    return {
        "simulated_fill": {"ticker": ticker, "side": "sell", "qty": qty, "price": price,
                             "pnl": pnl, "return_pct": return_pct, "remaining_shares": max(remaining, 0)},
        "training_example_labeled": close_result.get("labeled", False),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--action", required=True, choices=["BUY", "SELL"])
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--qty", type=int, required=True)
    parser.add_argument("--price", type=float, required=True,
                         help="the simulated tick's bar price -- there's no live quote to fall back to")
    parser.add_argument("--timestamp", required=True,
                         help="ISO 8601, timezone-aware (e.g. 2026-06-01T10:30:00-04:00) -- the simulated "
                              "tick's date/time, fed to gate_hours' context['_test_now'] override so the "
                              "hours gate judges against the replayed date instead of the real wall clock. "
                              "Without this, a backtest session could never place an order except during "
                              "real, current market hours, which defeats the point of an off-hours harness.")
    parser.add_argument("--conviction", type=float, default=None)
    parser.add_argument("--sector", default=None)
    parser.add_argument("--thesis", default=None)
    parser.add_argument("--play-type", default="standard", choices=["standard", "long", "conviction"])
    parser.add_argument("--predicted-by-date", default=None)
    parser.add_argument("--prediction-reason", default=None)
    parser.add_argument("--thesis-claim", default=None,
                         help="The falsifiable directional claim. Hard-required for --play-type long/conviction, "
                              "same as executor.py.")
    parser.add_argument("--thesis-invalidation", default=None,
                         help="The specific, checkable condition that would prove --thesis-claim wrong. "
                              "Hard-required for --play-type long/conviction, same as executor.py.")
    parser.add_argument("--close-reason", default=None, help="SELL only")
    parser.add_argument("--features", default=None, help="JSON object, same shape as executor.py's --features")
    args = parser.parse_args()

    ticker = args.ticker.upper()

    try:
        sim_now = datetime.datetime.fromisoformat(args.timestamp)
    except ValueError:
        print(json.dumps({"error": f"--timestamp must be ISO 8601: {args.timestamp!r}"}))
        return 1
    if sim_now.tzinfo is None:
        print(json.dumps({"error": "--timestamp must be timezone-aware (e.g. ...-04:00 for ET)"}))
        return 1

    try:
        db_path = backtest_session.resolve_session_db_path(args.session_id)
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e)}))
        return 1

    if args.play_type in ("long", "conviction") and not args.thesis_invalidation:
        print(json.dumps({
            "error": f"--play-type {args.play_type} requires --thesis-invalidation "
                     "(the specific, checkable condition that would prove this wrong -- "
                     "a conviction/long play held on an unfalsifiable thesis is just hoping)",
        }))
        return 1

    features = {}
    if args.features:
        try:
            features = json.loads(args.features)
            if not isinstance(features, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            print(json.dumps({"error": "--features must be a JSON object"}))
            return 1

    context = backtest_session.compute_context(args.session_id, current_prices={ticker: args.price})
    context["_test_now"] = sim_now
    trade_action = {
        "action": args.action, "ticker": ticker, "quantity": args.qty,
        "price": args.price, "conviction": args.conviction, "sector": args.sector,
        "play_type": args.play_type,
    }
    granted, reason, gate_results = executor.run_gates(context, trade_action, toggles=_backtest_toggles())
    if not granted:
        print(json.dumps({"error": f"guardrail: {reason}", "gates": gate_results}, indent=2))
        return 1

    if args.action == "BUY":
        if not args.thesis:
            print(json.dumps({"warning": "BUY executed with no --thesis provided"}), file=sys.stderr)
        result = _simulate_buy(
            args.session_id, db_path, ticker, args.qty, args.price, sim_now.isoformat(), args.conviction,
            args.sector, args.thesis, args.play_type, args.predicted_by_date,
            args.prediction_reason, features,
            thesis_claim=args.thesis_claim, thesis_invalidation=args.thesis_invalidation,
        )
    else:
        result = _simulate_sell(
            args.session_id, db_path, ticker, args.qty, args.price, sim_now.isoformat(), args.close_reason,
        )
        if "error" in result:
            print(json.dumps(result, indent=2))
            return 1

    result["gates"] = gate_results
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
