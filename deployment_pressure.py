#!/usr/bin/env python3
"""deployment_pressure.py — tracks how long the account has been sitting on
too much cash, and turns sustained pressure into (a) a lower conviction
floor and (b) escalating research effort. Mirrors bankroll.py's
days_remaining/endgame_factor shape: flat during a grace period, then ramps.

Raf, 2026-07-27: "if we go through a long period of not doing anything and
we still have a ton of cash not deployed, i want us to spend more time
trying to find an opportunity... lower the confidence required to make a
buy by still holding reasonably to certain hard limits."

"Ticks" here means executor.py `--action status` calls during market hours
(~300s cadence, tick_prompt.md step 4, already run every tick) — record_tick()
is called from there so this never costs an extra Alpaca call.

Usage:
    python3 deployment_pressure.py                       # print current state
    python3 deployment_pressure.py --mark-escalated freeform
    python3 deployment_pressure.py --mark-escalated backtest
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_FILE = Path(__file__).parent / "state" / "deployment_pressure.json"

# Reviewed-with-Raf constants, not params.json -- same reasoning bankroll.py
# gives for COMPETITION_END/ENDGAME_WINDOW_DAYS: isolated, easy to retune,
# not logic scattered through a function.
GRACE_TICKS = 12                        # ~1hr under-deployed before the floor gives at all
RAMP_TICKS = 60                         # ~5 more hours to fully bottom out (~1 trading day total)
CONVICTION_FLOOR_MIN_DEFAULT = 0.35     # fallback if params.json risk.conviction_floor_min is missing

FREEFORM_ESCALATION_TICKS = 36          # ~3hrs sustained -- escalate to freeform-discovery
FREEFORM_ESCALATION_COOLDOWN_HOURS = 4
BACKTEST_ESCALATION_TICKS = 78          # ~1 full trading day sustained -- escalate to replay_check.py
BACKTEST_ESCALATION_COOLDOWN_HOURS = 24

MIN_TICK_INTERVAL_SECONDS = 240  # ~4min, just under the real 5-min tick cadence.
# 2026-07-27, found live: record_tick() can be called more than once per
# real tick (e.g. Stan re-checking status mid-tick after a gated probe
# attempt) -- without this guard, every ramp/escalation constant above
# (all tuned in "ticks" assuming 1 call == 1 real ~5min tick) runs far
# faster in wall-clock terms than designed. Confirmed: the streak hit 1481
# in ~90 real minutes, where true tick cadence would give ~18.


def read_state() -> dict:
    default = {"consecutive_under_deployed_ticks": 0, "last_cash_pct": 0.0,
               "last_updated": None, "last_freeform_escalation_ts": None,
               "last_backtest_escalation_ts": None}
    if not STATE_FILE.exists():
        return default
    try:
        data = json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return default
    default.update(data)
    return default


def write_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def record_tick(cash_pct: float, threshold_pct: float, now: datetime = None) -> dict:
    """Called from executor.py's `status` action -- cash_pct/threshold_pct
    are already computed there for free (same account fetch
    discovery_urgency_check.py's under_deployed check uses). Only actually
    increments/resets the streak once per MIN_TICK_INTERVAL_SECONDS of real
    elapsed time -- a call sooner than that just refreshes last_cash_pct so
    calling this more than once within the same real tick can't inflate the
    counter (see MIN_TICK_INTERVAL_SECONDS above for why this exists)."""
    now = now or datetime.now(timezone.utc)
    state = read_state()

    last_updated_str = state.get("last_updated")
    if last_updated_str:
        elapsed = (now - datetime.fromisoformat(last_updated_str)).total_seconds()
        if elapsed < MIN_TICK_INTERVAL_SECONDS:
            state["last_cash_pct"] = cash_pct
            write_state(state)
            return state

    if cash_pct >= threshold_pct:
        state["consecutive_under_deployed_ticks"] = state.get("consecutive_under_deployed_ticks", 0) + 1
    else:
        state["consecutive_under_deployed_ticks"] = 0
    state["last_cash_pct"] = cash_pct
    state["last_updated"] = now.isoformat()
    write_state(state)
    return state


def reset(now: datetime = None) -> dict:
    """Called on a real BUY execution -- deploying capital (even a 1-share
    probe) is real evidence pressure eased; the next status call recomputes
    cash_pct fresh, so this can't create a stuck loophole."""
    state = read_state()
    state["consecutive_under_deployed_ticks"] = 0
    state["last_updated"] = (now or datetime.now(timezone.utc)).isoformat()
    write_state(state)
    return state


def conviction_floor(base_floor: float, floor_min: float, consecutive_ticks: int,
                      grace_ticks: int = GRACE_TICKS, ramp_ticks: int = RAMP_TICKS) -> float:
    """Flat at base_floor through the grace period (being under-deployed for
    a few ticks is normal, not a crisis) -- Raf: 'lower the confidence
    required ... still holding reasonably to certain hard limits', so this
    never drops below floor_min no matter how long the streak runs."""
    if consecutive_ticks <= grace_ticks:
        return base_floor
    progress = min(1.0, (consecutive_ticks - grace_ticks) / ramp_ticks) if ramp_ticks > 0 else 1.0
    return base_floor - progress * (base_floor - floor_min)


def research_escalation(state: dict, now: datetime = None) -> dict:
    """Whether THIS tick should escalate research effort -- gated by both
    persistence (ticks) and its own cooldown, so a long streak doesn't
    refire the expensive path every 5 minutes once past threshold."""
    now = now or datetime.now(timezone.utc)
    ticks = state.get("consecutive_under_deployed_ticks", 0)
    result = {"escalate_freeform_research": False, "escalate_backtest_check": False}

    def _due(last_ts, cooldown_hours):
        if last_ts is None:
            return True
        return (now - datetime.fromisoformat(last_ts)) >= timedelta(hours=cooldown_hours)

    if ticks >= FREEFORM_ESCALATION_TICKS and _due(state.get("last_freeform_escalation_ts"), FREEFORM_ESCALATION_COOLDOWN_HOURS):
        result["escalate_freeform_research"] = True
    if ticks >= BACKTEST_ESCALATION_TICKS and _due(state.get("last_backtest_escalation_ts"), BACKTEST_ESCALATION_COOLDOWN_HOURS):
        result["escalate_backtest_check"] = True
    return result


def mark_escalated(kind: str, now: datetime = None) -> dict:
    assert kind in ("freeform", "backtest")
    state = read_state()
    state[f"last_{kind}_escalation_ts"] = (now or datetime.now(timezone.utc)).isoformat()
    write_state(state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mark-escalated", choices=["freeform", "backtest"])
    args = parser.parse_args()
    if args.mark_escalated:
        print(json.dumps(mark_escalated(args.mark_escalated)))
        return
    print(json.dumps(read_state()))


if __name__ == "__main__":
    main()
