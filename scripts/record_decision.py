#!/usr/bin/env python3
"""CLI wrapper around decisions.py — logs a real decision/close, called
directly from tick_prompt.md via shell exec (not through the standalone
Trading Terminal, which stays unwired). Reuses the exact modules already
tested there (db.py, decisions.py, signals.py).

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
    sub = parser.add_subparsers(dest="command", required=True)

    dec = sub.add_parser("decision", help="Log a BUY/SELL/HOLD decision")
    dec.add_argument("--trader-id", default="stonks")
    dec.add_argument("--ticker", required=True)
    dec.add_argument("--action", required=True, choices=["BUY", "SELL", "HOLD"])
    dec.add_argument("--rationale", default="")
    dec.add_argument("--conviction", type=float, default=0.0)
    dec.add_argument("--regime", default=None)
    dec.add_argument("--features", default="{}", help="JSON string, per-signal shape (see signals.py)")

    close = sub.add_parser("close", help="Label a closed trade's outcome")
    close.add_argument("--trader-id", default="stonks")
    close.add_argument("--ticker", required=True)
    close.add_argument("--pnl", type=float, required=True)
    close.add_argument("--return-pct", type=float, required=True)

    args = parser.parse_args()

    if args.command == "decision":
        try:
            features = json.loads(args.features)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"--features not valid JSON: {e}"}))
            sys.exit(1)
        result = decisions.record_decision(
            trader_id=args.trader_id, ticker=args.ticker, action=args.action,
            rationale=args.rationale, conviction=args.conviction,
            regime=args.regime, features=features,
        )
        # Echo the reconciled cross-signal read back so it's visible in the
        # tick's tool output, not just stored — reconcile_signals() existed
        # but nothing ever surfaced its result until now. Scorecard-adjusted
        # if state/signal_scorecard.json exists and has scored signals, plain
        # fixed-weight otherwise.
        result["reconciled"] = signals.reconcile_signals(features, scorecard=_load_scorecard())
    else:
        result = decisions.record_trade_close(
            trader_id=args.trader_id, ticker=args.ticker,
            trade_id=None,  # Stonks has no trading.trades sync of its own — see decisions.py
            pnl=args.pnl, return_pct=args.return_pct,
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
