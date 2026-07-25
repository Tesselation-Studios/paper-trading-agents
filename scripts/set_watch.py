#!/usr/bin/env python3
"""Conditional orders Stan can pre-authorize for position_stream.py (the Alpaca
websocket daemon in paper-trading-rebuild) to execute mechanically, faster than
the next 5-min tick, when a condition is met — without a fresh LLM judgment at
execution time.

Usage:
  python3 scripts/set_watch.py add --ticker IP --field price --comparator ">=" \
    --value 45.00 --action SELL --qty all \
    --reason "profit target guide hit, thesis intact"

  python3 scripts/set_watch.py add --ticker BFST --field price --comparator "<=" \
    --value 28.50 --action BUY --qty 3 --conviction 0.65 --sector Financials \
    --reason "pretty sure on this dip, want in if it touches 28.50"

  python3 scripts/set_watch.py list
  python3 scripts/set_watch.py clear --id <id>
  python3 scripts/set_watch.py clear --ticker IP

SAFETY (see skills/ for the full design writeup):
  - field is restricted to "price"/"pnl_pct" — a structured comparison, never
    eval'd code, so position_stream.py's evaluation stays a simple, testable
    comparison, not an arbitrary-expression risk.
  - Execution (once triggered) always runs through executor.py's normal
    guardrail-gated check_order()/place_order() path — nothing here bypasses
    cash/position-size/sector/conviction/drawdown gates.
  - BUY watches expire within 5 minutes of creation, hard-capped — a stale
    pre-set BUY commits new capital on possibly-outdated reasoning, unlike a
    stale SELL (which only protects capital already at risk). If Stan still
    wants it after 5 minutes, that's a fresh tick's decision, not a standing
    order. SELL/ALERT watches default to end of the current trading day and
    are capped there too — no watch survives into a new session unnoticed.
  - BUY requires --conviction explicitly. executor.py's own conviction gate
    fails OPEN if conviction is omitted (fine for a live, judged tick) — but a
    watch has no fresh judgment at trigger time, so this script requires it
    up front rather than relying on that gate alone.
  - One-shot: position_stream.py removes a watch once it fires, it does not
    re-trigger.
"""
import argparse
import contextlib
import datetime
import fcntl
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

WORKSPACE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = WORKSPACE_DIR / "state"
WATCHES_PATH = STATE_DIR / "watches.json"


@contextlib.contextmanager
def _locked():
    """Two independent processes touch watches.json — Stan's tick (add) and
    position_stream.py's daemon (list/clear on trigger) — so every
    load-modify-save cycle needs to be atomic across processes, not just
    within one. Advisory flock, held only for the duration of one command.
    Derives the lock path from STATE_DIR at call time (not a frozen
    module-level constant) so tests that monkeypatch STATE_DIR actually
    isolate the lock file too, not just watches.json itself."""
    STATE_DIR.mkdir(exist_ok=True)
    lock_path = STATE_DIR / "watches.json.lock"
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)

FIELDS = ("price", "pnl_pct")
COMPARATORS = (">=", "<=")
ACTIONS = ("SELL", "BUY", "ALERT")
BUY_MAX_TTL_SECONDS = 5 * 60


def _now_et() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo("America/New_York"))


def _market_close_today() -> datetime.datetime:
    now = _now_et()
    return now.replace(hour=16, minute=0, second=0, microsecond=0)


def load_watches() -> list:
    if not WATCHES_PATH.exists():
        return []
    try:
        return json.loads(WATCHES_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_watches(watches: list) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    WATCHES_PATH.write_text(json.dumps(watches, indent=2))


def prune_expired(watches: list) -> list:
    now = _now_et()
    kept = []
    for w in watches:
        try:
            expires = datetime.datetime.fromisoformat(w["expires_at"])
        except (KeyError, ValueError):
            continue  # malformed entry, drop it rather than let it linger
        if expires > now:
            kept.append(w)
    return kept


def _validate_add(args: argparse.Namespace) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if args.field not in FIELDS:
        return None, f"--field must be one of {FIELDS}, got {args.field!r}"
    if args.comparator not in COMPARATORS:
        return None, f"--comparator must be one of {COMPARATORS}, got {args.comparator!r}"
    if args.action not in ACTIONS:
        return None, f"--action must be one of {ACTIONS}, got {args.action!r}"

    now = _now_et()

    if args.action == "SELL":
        if args.qty != "all":
            try:
                qty = int(args.qty)
            except (TypeError, ValueError):
                return None, "--qty must be a positive integer or 'all' for SELL"
            if qty <= 0:
                return None, "--qty must be positive"
    elif args.action == "BUY":
        if args.qty is None or args.qty == "all":
            return None, "--qty must be a positive integer for BUY (no 'all')"
        try:
            qty = int(args.qty)
        except (TypeError, ValueError):
            return None, "--qty must be a positive integer for BUY"
        if qty <= 0:
            return None, "--qty must be positive"
        if args.conviction is None:
            return None, "--conviction is required for BUY watches (no fresh judgment at trigger time)"
        if not (0.0 <= args.conviction <= 1.0):
            return None, "--conviction must be between 0 and 1"
    # ALERT needs neither qty nor conviction.

    if args.expires_at:
        try:
            expires = datetime.datetime.fromisoformat(args.expires_at)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=ZoneInfo("America/New_York"))
        except ValueError:
            return None, f"--expires-at not a valid ISO8601 timestamp: {args.expires_at!r}"
    else:
        expires = None  # filled in below per-action

    if args.action == "BUY":
        max_expiry = now + datetime.timedelta(seconds=BUY_MAX_TTL_SECONDS)
        if expires is None:
            expires = max_expiry
        elif expires > max_expiry:
            return None, (
                f"BUY watches expire within 5 minutes max (requested {expires.isoformat()}, "
                f"latest allowed {max_expiry.isoformat()}) — re-set it at the next tick if still wanted"
            )
    else:
        close_today = _market_close_today()
        if expires is None:
            expires = close_today
        elif expires > close_today:
            return None, (
                f"SELL/ALERT watches can't outlive today's session (requested {expires.isoformat()}, "
                f"latest allowed {close_today.isoformat()}) — re-set it fresh next session if still wanted"
            )

    if expires <= now:
        return None, f"--expires-at must be in the future (got {expires.isoformat()}, now is {now.isoformat()})"

    watch = {
        "id": uuid.uuid4().hex[:8],
        "ticker": args.ticker.upper(),
        "field": args.field,
        "comparator": args.comparator,
        "value": args.value,
        "action": args.action,
        "qty": args.qty if args.action != "ALERT" else None,
        "conviction": args.conviction,
        "sector": args.sector,
        "reason": args.reason,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
    }
    return watch, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Set/list/clear conditional watches for position_stream.py")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Add a new watch condition")
    add.add_argument("--ticker", required=True)
    add.add_argument("--field", required=True, help=f"one of {FIELDS}")
    add.add_argument("--comparator", required=True, help=f"one of {COMPARATORS}")
    add.add_argument("--value", required=True, type=float)
    add.add_argument("--action", required=True, help=f"one of {ACTIONS}")
    add.add_argument("--qty", help="positive int, or 'all' for SELL only")
    add.add_argument("--conviction", type=float, help="required for BUY")
    add.add_argument("--sector", default=None, help="optional, auto-derived if omitted (BUY only)")
    add.add_argument("--reason", required=True)
    add.add_argument("--expires-at", default=None, help="ISO8601; defaults per-action (BUY: +5min cap, SELL/ALERT: today's close)")

    lst = sub.add_parser("list", help="List active watches (expired ones pruned first)")
    lst.add_argument("--ticker", default=None)

    clr = sub.add_parser("clear", help="Remove a watch by id or all watches for a ticker")
    clr.add_argument("--id", default=None)
    clr.add_argument("--ticker", default=None)

    args = parser.parse_args()

    if args.command == "add":
        watch, error = _validate_add(args)
        if error:
            print(json.dumps({"error": error}))
            return 1
        with _locked():
            watches = prune_expired(load_watches())
            watches.append(watch)
            save_watches(watches)
        print(json.dumps({"ok": True, "watch": watch}, indent=2))
        return 0

    if args.command == "list":
        with _locked():
            watches = prune_expired(load_watches())
            save_watches(watches)  # persist the prune, not just this call's view
        if args.ticker:
            watches = [w for w in watches if w["ticker"] == args.ticker.upper()]
        print(json.dumps({"watches": watches}, indent=2))
        return 0

    if args.command == "clear":
        if not args.id and not args.ticker:
            print(json.dumps({"error": "--id or --ticker required"}))
            return 1
        with _locked():
            watches = prune_expired(load_watches())
            before = len(watches)
            if args.id:
                watches = [w for w in watches if w["id"] != args.id]
            if args.ticker:
                watches = [w for w in watches if w["ticker"] != args.ticker.upper()]
            save_watches(watches)
        print(json.dumps({"ok": True, "removed": before - len(watches)}))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
