#!/usr/bin/env python3
"""stop_patience.py — replaces a flat stop-loss/trailing-stop distance with
one that widens the longer a position has been held (Raf, 2026-07-27: "stan
can also turn bad buys into good ones sometimes by simply holding longer...
we have a few months"), then compresses back to the tight/base distance
inside the endgame window before COMPETITION_END so a position can't be
"held out" past the deadline. Same days_remaining/endgame_factor shape as
bankroll.py -- imports it directly rather than re-declaring COMPETITION_END,
so there's one source of truth for the deadline.
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bankroll  # noqa: E402 -- single source of truth for COMPETITION_END/days_remaining

PATIENCE_RAMP_DAYS = 45              # days held to reach full patience (no further widening)
ENDGAME_TIGHTEN_WINDOW_DAYS = 30     # inside this many days of the deadline, patience compresses to 0


def patience_progress(entry_date: date, today: date = None,
                       ramp_days: int = PATIENCE_RAMP_DAYS,
                       endgame_window_days: int = ENDGAME_TIGHTEN_WINDOW_DAYS) -> float:
    """0.0 (just entered, or at the deadline) -> 1.0 (fully patient)."""
    today = today or datetime.now(timezone.utc).date()
    days_held = max(0, (today - entry_date).days)
    progress = min(1.0, days_held / ramp_days) if ramp_days > 0 else 1.0

    remaining = bankroll.days_remaining(today)
    if remaining < endgame_window_days:
        progress *= max(0.0, remaining / endgame_window_days)
    return progress


def _interp(base: float, outer: float, progress: float) -> float:
    return base + progress * (outer - base)


def effective_hard_stop_pct(entry_date: date, today: date = None,
                             base_pct: float = -6.0, max_pct: float = -20.0) -> float:
    """base_pct/max_pct are negative (loss) percentages -- pass params.json's
    risk.stop_loss_pct/stop_loss_pct_max values directly, already negative.
    Returns a negative percentage; callers abs() once at the call site
    rather than juggling signs twice, which is exactly the kind of bug that
    would silently make the stop tighter instead of wider."""
    return _interp(base_pct, max_pct, patience_progress(entry_date, today))


def effective_trailing_stop_pct(entry_date: date, today: date = None,
                                 base_pct: float = 5.0, max_pct: float = 12.0) -> float:
    """base_pct/max_pct are positive (distance below peak) percentages."""
    return _interp(base_pct, max_pct, patience_progress(entry_date, today))
