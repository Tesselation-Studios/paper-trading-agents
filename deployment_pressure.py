#!/usr/bin/env python3
"""deployment_pressure.py — tracks how long the account has been sitting on
too much cash, and turns sustained pressure into (a) a lower conviction
floor and (b) escalating research effort. Mirrors bankroll.py's
days_remaining/endgame_factor shape: flat during a grace period, then ramps.

Raf, 2026-07-27: "if we go through a long period of not doing anything and
we still have a ton of cash not deployed, i want us to spend more time
trying to find an opportunity... lower the confidence required to make a
buy by still holding reasonably to certain hard limits."

"Ticks" here means real trade ticks -- one per tick_prompt.md execution,
i.e. one per `stonks-tick` cron firing (params.json tick.interval_seconds,
300s during market hours) -- NOT wall-clock elapsed time and NOT "however
many times executor.py --action status happens to be called." status gets
called more than once within a single tick's session sometimes (e.g. Stan
re-checking it mid-tick after a gated probe attempt); record_tick() must
still only count that as ONE tick. See _tick_bucket() below: identity is
derived by rounding `now` down to the tick schedule's own fixed interval,
not by measuring time since the last call -- a session that happens to run
long doesn't get miscounted as two ticks, and this needs no cooperation
from the caller (no risk of "forgot to only call this once").

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

# Fallback only -- executor.py's status action reads the real value from
# params.json tick.interval_seconds and passes it in, so this constant
# stays correct even if the schedule itself is retuned.
DEFAULT_TICK_INTERVAL_SECONDS = 300

# 2026-07-27, found live: record_tick() was being called more than once per
# real tick, and the original fix (a sliding wall-clock debounce window)
# was itself the wrong model -- Raf: "each tick should be each trade tick
# that runs, not wall time." Replaced with _tick_bucket() below: identity
# tied to the actual schedule, not elapsed time since the last call.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _tick_bucket(now: datetime, interval_seconds: int) -> str:
    """Which real tick_prompt.md execution `now` falls into, aligned to the
    tick cron's own fixed schedule. Two record_tick() calls land in the
    same bucket (same tick) if and only if they occur during the same
    scheduled tick interval -- regardless of how far apart in wall time
    they happen to be within that interval, and regardless of whether a
    slow-running tick spills close to the boundary."""
    seconds_since_epoch = (now - _EPOCH).total_seconds()
    bucket_start = int(seconds_since_epoch // interval_seconds) * interval_seconds
    return str(bucket_start)


def read_state() -> dict:
    default = {"consecutive_under_deployed_ticks": 0, "last_cash_pct": 0.0,
               "last_updated": None, "last_tick_key": None,
               "last_freeform_escalation_ts": None,
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


def record_tick(cash_pct: float, threshold_pct: float, now: datetime = None,
                 interval_seconds: int = DEFAULT_TICK_INTERVAL_SECONDS) -> dict:
    """Called from executor.py's `status` action -- cash_pct/threshold_pct
    are already computed there for free (same account fetch
    discovery_urgency_check.py's under_deployed check uses). Only actually
    increments/resets the streak on a NEW tick bucket (see _tick_bucket) --
    a call landing in the same bucket as the last one just refreshes
    last_cash_pct, so calling this more than once within the same real tick
    can't inflate the counter, no matter how many times or how far apart
    within that tick it's called."""
    now = now or datetime.now(timezone.utc)
    state = read_state()
    tick_key = _tick_bucket(now, interval_seconds)

    if state.get("last_tick_key") == tick_key:
        state["last_cash_pct"] = cash_pct
        write_state(state)
        return state

    if cash_pct >= threshold_pct:
        state["consecutive_under_deployed_ticks"] = state.get("consecutive_under_deployed_ticks", 0) + 1
    else:
        state["consecutive_under_deployed_ticks"] = 0
    state["last_cash_pct"] = cash_pct
    state["last_updated"] = now.isoformat()
    state["last_tick_key"] = tick_key
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
