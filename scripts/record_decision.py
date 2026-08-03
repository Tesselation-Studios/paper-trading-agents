#!/usr/bin/env python3
"""CLI wrapper around decisions.py — logs a real decision/close, called
directly from tick_prompt.md via shell exec (not through the standalone
Trading Terminal, which stays unwired). Reuses the exact modules already
tested there (db.py, decisions.py, signals.py).

2026-08-02: executor.py now also writes `decisions` rows directly on every
real BUY/SELL fill (mechanized, see executor.py::_record_decision_row) —
this CLI is no longer the only path to a decisions row, matching the same
2026-08-01 mechanization already done for training_examples via
record_entry_example(). This module is unchanged and still needed for: the
`reconcile` subcommand (called standalone before the executor BUY/SELL call,
per tick_prompt.md step 8, to get combined_confidence), and any manual/
backfill invocation.

Usage:
  python3 scripts/record_decision.py decision --ticker SOFI --action BUY \
    --conviction 0.6 --rationale "momentum entry, RSI 58" --regime momentum_bull \
    --features '{"sentiment": {"direction": "bullish", "confidence": 0.7}, "technical": {"direction": "bullish", "confidence": 0.6}}'

  python3 scripts/record_decision.py close --ticker SOFI --pnl 12.50 --return-pct 4.2
"""
import argparse
import json
import sys
from pathlib import Path

import decisions
import signals

SCORECARD_PATH = Path(__file__).resolve().parent.parent / "state" / "signal_scorecard.json"


def _load_scorecard():
    if not SCORECARD_PATH.exists():
        return None
    try:
        return json.loads(SCORECARD_PATH.read_text()).get("signals")
    except (json.JSONDecodeError, OSError):
        return None


def main():
    parser = argparse.ArgumentParser(description="Log a trading decision or trade close")
    parser.add_argument("--db-path", default=None, help="Override state/trader.db (dry-run/tests)")
    sub = parser.add_subparsers(dest="command", required=True)

    dec = sub.add_parser("decision", help="Log a BUY/SELL/HOLD decision")
    dec.add_argument("--trader-id", default="stonks")
    dec.add_argument("--ticker", required=True)
    dec.add_argument("--action", required=True, choices=["BUY", "SELL", "HOLD"])
    dec.add_argument("--rationale", default="")
    dec.add_argument("--conviction", type=float, default=None,
                      help="0-1. If omitted, self-computed from --features via "
                           "reconcile_signals() rather than silently logging 0.0")
    dec.add_argument("--regime", default=None)
    dec.add_argument("--features", default="{}", help="JSON string, per-signal shape (see signals.py)")

    rec = sub.add_parser("reconcile", help="Preview combined_confidence for signal features "
                          "before trading — no DB write, safe to call before the executor "
                          "decides anything. Its combined_confidence is what tick_prompt.md "
                          "now passes as --conviction to both the executor BUY call and the "
                          "later 'decision' log call for the same trade.")
    rec.add_argument("--features", default="{}")

    close = sub.add_parser("close", help="Label a closed trade's outcome")
    close.add_argument("--trader-id", default="stonks")
    close.add_argument("--ticker", required=True)
    close.add_argument("--pnl", type=float, required=True)
    close.add_argument("--return-pct", type=float, required=True)

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    if args.command == "reconcile":
        try:
            features = json.loads(args.features)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"--features not valid JSON: {e}"}))
            sys.exit(1)
        print(json.dumps(signals.reconcile_signals(features, scorecard=_load_scorecard())))
        return

    if args.command == "decision":
        try:
            features = json.loads(args.features)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"--features not valid JSON: {e}"}))
            sys.exit(1)
        if not features:
            # Silent otherwise -- an empty features blob on a real decision
            # means no signal actually got scored for it, which starves both
            # signal_scorecard.py's hit-rate analysis and any future ML
            # training on training_examples. Visible here so it shows up in
            # the tick's own output, not just quietly stored as {}.
            print(json.dumps({"warning": "decision recorded with no scored --features "
                                          "(training_examples.features will be empty)"}), file=sys.stderr)
        reconciled = signals.reconcile_signals(features, scorecard=_load_scorecard())
        conviction = args.conviction
        if conviction is None:
            # No explicit --conviction: self-compute rather than the old
            # silent default=0.0, which would have logged every omitted
            # call as zero conviction regardless of the actual signals.
            conviction = reconciled["combined_confidence"]
        result = decisions.record_decision(
            trader_id=args.trader_id, ticker=args.ticker, action=args.action,
            rationale=args.rationale, conviction=conviction,
            regime=args.regime, features=features, db_path=db_path,
        )
        # Echo the reconciled cross-signal read back so it's visible in the
        # tick's tool output, not just stored — reconcile_signals() existed
        # but nothing ever surfaced its result until now. Scorecard-adjusted
        # if state/signal_scorecard.json exists and has scored signals, plain
        # fixed-weight otherwise.
        result["reconciled"] = reconciled
    else:
        result = decisions.record_trade_close(
            trader_id=args.trader_id, ticker=args.ticker,
            trade_id=None,  # Stonks has no trading.trades sync of its own — see decisions.py
            pnl=args.pnl, return_pct=args.return_pct, db_path=db_path,
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
