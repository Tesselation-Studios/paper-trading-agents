#!/usr/bin/env python3
"""Unit tests for stop_patience.py — widens stop-loss/trailing-stop distance
the longer a position is held, compressing back to base near the deadline.
Mirrors tests/test_bankroll.py's TestDaysRemaining/TestEndgameFactor style."""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bankroll  # noqa: E402
import stop_patience as sp  # noqa: E402


class TestPatienceProgress:
    def test_zero_at_entry(self):
        today = date(2026, 7, 27)
        assert sp.patience_progress(today, today) == 0.0

    def test_ramps_up_with_days_held(self):
        entry = date(2026, 7, 1)
        mid = entry + timedelta(days=sp.PATIENCE_RAMP_DAYS // 2)
        progress = sp.patience_progress(entry, mid)
        assert 0.0 < progress < 1.0

    def test_reaches_full_patience_at_ramp_days(self):
        entry = date(2026, 7, 1)
        full = entry + timedelta(days=sp.PATIENCE_RAMP_DAYS)
        assert sp.patience_progress(entry, full) == pytest.approx(1.0)

    def test_never_exceeds_1_beyond_ramp_days(self):
        entry = date(2026, 7, 1)
        way_later = entry + timedelta(days=sp.PATIENCE_RAMP_DAYS * 3)
        assert sp.patience_progress(entry, way_later) == pytest.approx(1.0)

    def test_compresses_inside_endgame_window(self):
        # far in the past entry (fully ramped by day count), but "today" is
        # deep inside the endgame tighten window
        entry = date(2026, 1, 1)
        near_deadline = bankroll.COMPETITION_END - timedelta(days=5)
        progress = sp.patience_progress(entry, near_deadline)
        assert progress < 1.0

    def test_compresses_to_near_zero_right_at_deadline(self):
        entry = date(2026, 1, 1)
        progress = sp.patience_progress(entry, bankroll.COMPETITION_END)
        assert progress == pytest.approx(0.0, abs=1e-6)

    def test_unaffected_outside_endgame_window(self):
        entry = date(2026, 1, 1)
        # far from both entry-ramp boundary issues and the endgame window
        mid_year = date(2026, 6, 1)
        # fully ramped (>45 days held), and far outside the 30-day endgame window
        far_from_deadline = bankroll.COMPETITION_END - timedelta(days=sp.ENDGAME_TIGHTEN_WINDOW_DAYS + 60)
        progress = sp.patience_progress(entry, far_from_deadline)
        assert progress == pytest.approx(1.0)


class TestEffectiveHardStopPct:
    def test_day_zero_equals_base(self):
        today = date(2026, 7, 27)
        assert sp.effective_hard_stop_pct(today, today, base_pct=-10.0, max_pct=-20.0) == pytest.approx(-10.0)

    def test_full_ramp_equals_max(self):
        entry = date(2026, 7, 1)
        full = entry + timedelta(days=sp.PATIENCE_RAMP_DAYS)
        result = sp.effective_hard_stop_pct(entry, full, base_pct=-10.0, max_pct=-20.0)
        assert result == pytest.approx(-20.0)

    def test_gets_more_negative_not_tighter_as_days_held_increases(self):
        """Explicit sign-correctness test -- the exact bug class flagged
        during design: juggling signs twice could silently make the stop
        TIGHTER instead of wider as patience increases."""
        entry = date(2026, 7, 1)
        checkpoints = [entry + timedelta(days=d) for d in (0, 5, 15, 30, 45)]
        values = [sp.effective_hard_stop_pct(entry, d, base_pct=-10.0, max_pct=-20.0) for d in checkpoints]
        # every subsequent value must be <= the previous (more negative or equal)
        assert all(a >= b for a, b in zip(values, values[1:])), values
        assert values[0] == pytest.approx(-10.0)
        assert values[-1] == pytest.approx(-20.0)

    def test_endgame_compresses_back_toward_base_not_further_widened(self):
        entry = date(2026, 1, 1)  # fully ramped by day count
        near_deadline = bankroll.COMPETITION_END - timedelta(days=5)
        result = sp.effective_hard_stop_pct(entry, near_deadline, base_pct=-10.0, max_pct=-20.0)
        assert -10.0 >= result > -20.0  # more negative than base, but not fully widened


class TestEffectiveTrailingStopPct:
    def test_day_zero_equals_base(self):
        today = date(2026, 7, 27)
        assert sp.effective_trailing_stop_pct(today, today, base_pct=5.0, max_pct=12.0) == pytest.approx(5.0)

    def test_full_ramp_equals_max(self):
        entry = date(2026, 7, 1)
        full = entry + timedelta(days=sp.PATIENCE_RAMP_DAYS)
        result = sp.effective_trailing_stop_pct(entry, full, base_pct=5.0, max_pct=12.0)
        assert result == pytest.approx(12.0)

    def test_gets_wider_not_tighter_as_days_held_increases(self):
        entry = date(2026, 7, 1)
        checkpoints = [entry + timedelta(days=d) for d in (0, 5, 15, 30, 45)]
        values = [sp.effective_trailing_stop_pct(entry, d, base_pct=5.0, max_pct=12.0) for d in checkpoints]
        assert all(a <= b for a, b in zip(values, values[1:])), values
        assert values[0] == pytest.approx(5.0)
        assert values[-1] == pytest.approx(12.0)
