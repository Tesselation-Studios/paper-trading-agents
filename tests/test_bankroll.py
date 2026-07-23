#!/usr/bin/env python3
"""
Unit tests for the self-calibrating bankroll ceiling in bankroll.py.

Covers read/write round-trip, win/loss ceiling adjustment, floor/max caps,
growth-rate acceleration/deceleration based on win rate, target-profit-pct
shrinkage above $500 ceiling, and history log formatting/capping.

BANKROLL_FILE is monkeypatched to a tmp_path file in every test — this file
is live production state and must never be touched by tests.
"""
import datetime
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import bankroll  # noqa: E402


@pytest.fixture
def bankroll_file(tmp_path, monkeypatch):
    """Redirect BANKROLL_FILE to an isolated tmp path for every test."""
    path = tmp_path / "bankroll.md"
    monkeypatch.setattr(bankroll, "BANKROLL_FILE", path)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# read_bankroll — defaults + round-trip
# ─────────────────────────────────────────────────────────────────────────────


class TestReadBankrollDefaults:
    def test_defaults_when_file_missing(self, bankroll_file):
        assert not bankroll_file.exists()
        state = bankroll.read_bankroll()
        assert state["ceiling"] == bankroll.STARTING_CEILING
        assert state["growth_rate"] == bankroll.GROWTH_RATE
        assert state["decay_rate"] == bankroll.DECAY_RATE
        assert state["target_profit_pct"] == bankroll.TARGET_PROFIT_PCT
        assert state["closed_trades"] == 0
        assert state["wins"] == 0
        assert state["losses"] == 0
        assert state["net_pnl"] == 0.0
        assert state["history"] == []


class TestReadWriteRoundTrip:
    def test_round_trip_preserves_core_fields(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 123.45
        state["growth_rate"] = 0.03
        state["decay_rate"] = 0.02  # 2 decimals — write_bankroll only keeps .2f precision
        state["target_profit_pct"] = 0.8
        state["closed_trades"] = 7
        state["wins"] = 5
        state["losses"] = 2
        state["net_pnl"] = 12.34
        state["history"] = ["07/22 10:00 WIN $+5.00 → $50.00"]

        bankroll.write_bankroll(state)
        assert bankroll_file.exists()

        reread = bankroll.read_bankroll()
        assert reread["ceiling"] == 123.45
        assert reread["growth_rate"] == 0.03
        assert reread["decay_rate"] == 0.02
        assert reread["target_profit_pct"] == 0.8
        assert reread["closed_trades"] == 7
        assert reread["wins"] == 5
        assert reread["losses"] == 2
        assert reread["net_pnl"] == 12.34
        # read_bankroll retains the "-- " prefix from the on-disk format
        assert reread["history"] == ["-- 07/22 10:00 WIN $+5.00 → $50.00"]


# ─────────────────────────────────────────────────────────────────────────────
# recalc_ceiling — win/loss adjustment
# ─────────────────────────────────────────────────────────────────────────────


class TestRecalcCeilingWinLoss:
    def test_ceiling_grows_2pct_on_win(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 100.0
        bankroll.recalc_ceiling(state, pnl=5.0, is_win=True)
        assert state["ceiling"] == pytest.approx(102.0)
        assert state["wins"] == 1
        assert state["closed_trades"] == 1
        assert state["net_pnl"] == pytest.approx(5.0)

    def test_ceiling_shrinks_1pct_on_loss(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 100.0
        bankroll.recalc_ceiling(state, pnl=-5.0, is_win=False)
        assert state["ceiling"] == pytest.approx(99.0)
        assert state["losses"] == 1
        assert state["closed_trades"] == 1
        assert state["net_pnl"] == pytest.approx(-5.0)


# ─────────────────────────────────────────────────────────────────────────────
# recalc_ceiling — floor / max caps
# ─────────────────────────────────────────────────────────────────────────────


class TestRecalcCeilingCaps:
    def test_floor_cap_respected(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 50.5  # just above the $50 floor
        bankroll.recalc_ceiling(state, pnl=-1.0, is_win=False)
        # 50.5 * 0.99 = 49.995, which is below FLOOR -> clamped to 50.0
        assert state["ceiling"] == pytest.approx(bankroll.FLOOR)

    def test_floor_cap_respected_at_exact_floor(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = bankroll.FLOOR
        bankroll.recalc_ceiling(state, pnl=-1.0, is_win=False)
        assert state["ceiling"] == pytest.approx(bankroll.FLOOR)

    def test_max_ceiling_cap_respected(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 1990.0
        state["growth_rate"] = 0.02
        bankroll.recalc_ceiling(state, pnl=50.0, is_win=True)
        # 1990 * 1.02 = 2029.8, above MAX_CEILING -> clamped to 2000.0
        assert state["ceiling"] == pytest.approx(bankroll.MAX_CEILING)


# ─────────────────────────────────────────────────────────────────────────────
# recalc_ceiling — growth-rate acceleration / deceleration
# ─────────────────────────────────────────────────────────────────────────────


class TestGrowthRateCalibration:
    def test_growth_rate_accelerates_above_55pct_win_rate(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 100.0
        state["closed_trades"] = 9
        state["wins"] = 6
        state["losses"] = 3
        # 10th trade, a win -> 7/10 = 70% win rate, > 0.55 threshold
        bankroll.recalc_ceiling(state, pnl=5.0, is_win=True)
        assert state["closed_trades"] == 10
        assert state["wins"] == 7
        # bonus = min(0.03, (0.7 - 0.55) * 0.15) = 0.0225
        expected_rate = round(min(0.08, bankroll.GROWTH_RATE + 0.0225), 4)
        assert state["growth_rate"] == pytest.approx(expected_rate)
        assert state["growth_rate"] > bankroll.GROWTH_RATE

    def test_growth_rate_decelerates_below_45pct_win_rate(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 100.0
        state["closed_trades"] = 9
        state["wins"] = 3
        state["losses"] = 6
        # 10th trade, a loss -> 3/10 = 30% win rate, < 0.45 threshold
        bankroll.recalc_ceiling(state, pnl=-5.0, is_win=False)
        assert state["closed_trades"] == 10
        expected_rate = round(max(0.01, bankroll.GROWTH_RATE - 0.003), 4)
        assert state["growth_rate"] == pytest.approx(expected_rate)
        assert state["growth_rate"] < bankroll.GROWTH_RATE

    def test_growth_rate_untouched_below_10_trades(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 100.0
        state["closed_trades"] = 5
        state["wins"] = 5
        state["losses"] = 0
        bankroll.recalc_ceiling(state, pnl=5.0, is_win=True)
        assert state["closed_trades"] == 6  # still under 10
        assert state["growth_rate"] == bankroll.GROWTH_RATE

    def test_growth_rate_untouched_between_45_and_55pct(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 100.0
        state["closed_trades"] = 9
        state["wins"] = 5
        state["losses"] = 4
        # 10th trade a win -> 6/10 = 60%... use a win rate that lands in the
        # neutral band instead: 5 wins / 10 = 50%
        bankroll.recalc_ceiling(state, pnl=5.0, is_win=False)
        # closed_trades=10, wins stayed 5 (loss recorded), win_rate = 5/10 = 50%
        assert state["closed_trades"] == 10
        assert state["wins"] == 5
        assert state["growth_rate"] == bankroll.GROWTH_RATE


# ─────────────────────────────────────────────────────────────────────────────
# recalc_ceiling — target_profit_pct shrinkage above $500 ceiling
# ─────────────────────────────────────────────────────────────────────────────


class TestTargetProfitPctShrinkage:
    def test_target_profit_unchanged_at_or_below_500(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 300.0
        state["growth_rate"] = 0.0  # keep ceiling fixed for a clean assertion
        bankroll.recalc_ceiling(state, pnl=1.0, is_win=True)
        assert state["ceiling"] == pytest.approx(300.0)
        assert state["target_profit_pct"] == bankroll.TARGET_PROFIT_PCT

    def test_target_profit_shrinks_above_500(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 700.0
        state["growth_rate"] = 0.0  # keep ceiling fixed at 700 for this assertion
        bankroll.recalc_ceiling(state, pnl=1.0, is_win=True)
        assert state["ceiling"] == pytest.approx(700.0)
        # (700 - 500) * 0.0005 = 0.1 -> 1.0 - 0.1 = 0.9
        assert state["target_profit_pct"] == pytest.approx(0.9)

    def test_target_profit_floor_at_half_percent(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = bankroll.MAX_CEILING  # 2000
        state["growth_rate"] = 0.0
        bankroll.recalc_ceiling(state, pnl=1.0, is_win=True)
        # (2000 - 500) * 0.0005 = 0.75 -> 1.0 - 0.75 = 0.25, floored to 0.5
        assert state["target_profit_pct"] == pytest.approx(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# History log — formatting + 50-entry cap
# ─────────────────────────────────────────────────────────────────────────────


class TestHistoryLog:
    HISTORY_ENTRY_RE = re.compile(
        r"^\d{2}/\d{2} \d{2}:\d{2} (WIN|LOSS) \$[+-]\d+\.\d{2} → \$\d+\.\d{2}$"
    )

    def test_win_entry_is_formatted(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 100.0
        bankroll.recalc_ceiling(state, pnl=10.0, is_win=True)
        assert len(state["history"]) == 1
        assert self.HISTORY_ENTRY_RE.match(state["history"][-1])
        assert "WIN" in state["history"][-1]
        assert "$+10.00" in state["history"][-1]

    def test_loss_entry_is_formatted(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["ceiling"] = 100.0
        bankroll.recalc_ceiling(state, pnl=-7.5, is_win=False)
        assert len(state["history"]) == 1
        assert self.HISTORY_ENTRY_RE.match(state["history"][-1])
        assert "LOSS" in state["history"][-1]
        assert "$-7.50" in state["history"][-1]

    def test_history_capped_at_50_on_write(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["history"] = [f"07/22 10:{i:02d} WIN $+1.00 → $50.00" for i in range(55)]
        bankroll.write_bankroll(state)

        reread = bankroll.read_bankroll()
        assert len(reread["history"]) == 50
        # the oldest 5 entries should have been dropped, newest 50 kept
        original_last_50 = state["history"][-50:]
        stripped_reread = [h.removeprefix("-- ") for h in reread["history"]]
        assert stripped_reread == original_last_50

    def test_no_history_placeholder_when_empty(self, bankroll_file):
        state = bankroll.read_bankroll()
        state["history"] = []
        bankroll.write_bankroll(state)
        text = bankroll_file.read_text()
        assert "(no closed trades yet)" in text


class TestUniverseMaxPriceForCeiling:
    """2026-07-23: replaces the dead experience.json.peak_ceiling milestone
    table in TOOLS.md (stuck at $50 since creation, never fired) with one
    real, mechanized connection between bankroll growth and universe
    breadth."""

    def test_starting_ceiling_keeps_current_universe(self):
        assert bankroll.universe_max_price_for_ceiling(bankroll.STARTING_CEILING) == 50.0

    def test_real_current_ceiling_keeps_current_universe(self):
        # Real live ceiling as of 2026-07-23 is $51 -- barely above floor,
        # should not have unlocked anything yet.
        assert bankroll.universe_max_price_for_ceiling(51.0) == 50.0

    def test_first_tier_boundary(self):
        assert bankroll.universe_max_price_for_ceiling(99.99) == 50.0
        assert bankroll.universe_max_price_for_ceiling(100.0) == 75.0

    def test_second_tier_boundary(self):
        assert bankroll.universe_max_price_for_ceiling(299.99) == 75.0
        assert bankroll.universe_max_price_for_ceiling(300.0) == 150.0

    def test_third_tier_boundary(self):
        assert bankroll.universe_max_price_for_ceiling(749.99) == 150.0
        assert bankroll.universe_max_price_for_ceiling(750.0) == 300.0

    def test_at_max_ceiling_returns_widest_tier(self):
        assert bankroll.universe_max_price_for_ceiling(bankroll.MAX_CEILING) == 300.0

    def test_monotonically_non_decreasing_across_tiers(self):
        ceilings = [50, 99, 100, 250, 300, 500, 750, 1000, 2000]
        prices = [bankroll.universe_max_price_for_ceiling(c) for c in ceilings]
        assert prices == sorted(prices)


class TestDaysRemaining:
    def test_before_deadline(self):
        assert bankroll.days_remaining(datetime.date(2026, 12, 1)) == 30

    def test_on_deadline(self):
        assert bankroll.days_remaining(datetime.date(2026, 12, 31)) == 0

    def test_after_deadline_clamps_to_zero(self):
        assert bankroll.days_remaining(datetime.date(2027, 1, 15)) == 0


class TestEndgameFactor:
    def test_neutral_outside_endgame_window(self):
        assert bankroll.endgame_factor(datetime.date(2026, 9, 1)) == 1.0

    def test_exactly_at_window_boundary_is_neutral(self):
        # 60 days out is still >= ENDGAME_WINDOW_DAYS -> neutral
        boundary = bankroll.COMPETITION_END - datetime.timedelta(days=bankroll.ENDGAME_WINDOW_DAYS)
        assert bankroll.endgame_factor(boundary) == 1.0

    def test_ramps_up_inside_window(self):
        halfway = bankroll.COMPETITION_END - datetime.timedelta(days=bankroll.ENDGAME_WINDOW_DAYS // 2)
        factor = bankroll.endgame_factor(halfway)
        assert 1.0 < factor < bankroll.ENDGAME_MAX_MULTIPLIER

    def test_max_at_deadline(self):
        assert bankroll.endgame_factor(bankroll.COMPETITION_END) == pytest.approx(
            bankroll.ENDGAME_MAX_MULTIPLIER)

    def test_monotonically_increasing_toward_deadline(self):
        days_out = [90, 60, 45, 30, 15, 1, 0]
        dates = [bankroll.COMPETITION_END - datetime.timedelta(days=d) for d in days_out]
        factors = [bankroll.endgame_factor(d) for d in dates]
        assert factors == sorted(factors)


class TestPerformanceFactor:
    def test_behind_starting_capital_boosts(self):
        assert bankroll.performance_factor(9000, starting_capital=10000) == bankroll.BEHIND_PACE_MULTIPLIER

    def test_at_starting_capital_neutral(self):
        assert bankroll.performance_factor(10000, starting_capital=10000) == 1.0

    def test_modest_lead_neutral(self):
        assert bankroll.performance_factor(12000, starting_capital=10000) == 1.0

    def test_meaningful_lead_dampens(self):
        assert bankroll.performance_factor(15000, starting_capital=10000) == bankroll.AHEAD_PACE_MULTIPLIER

    def test_zero_starting_capital_does_not_crash(self):
        assert bankroll.performance_factor(1000, starting_capital=0) == 1.0


class TestCompetitionMultiplier:
    def test_neutral_midyear_at_starting_capital(self):
        mult = bankroll.competition_multiplier(10000, today=datetime.date(2026, 9, 1))
        assert mult == pytest.approx(1.0)

    def test_combines_both_factors(self):
        # Behind pace AND in the endgame window -> both factors > 1, combined higher than either alone
        endgame_date = bankroll.COMPETITION_END - datetime.timedelta(days=10)
        mult = bankroll.competition_multiplier(9000, today=endgame_date)
        assert mult > bankroll.BEHIND_PACE_MULTIPLIER
        assert mult > bankroll.endgame_factor(endgame_date)

    def test_clamped_to_bounds(self):
        lo, hi = bankroll.COMBINED_MULTIPLIER_BOUNDS
        mult = bankroll.competition_multiplier(9000, today=bankroll.COMPETITION_END)
        assert lo <= mult <= hi

    def test_ahead_and_not_endgame_dampens_below_one(self):
        mult = bankroll.competition_multiplier(20000, today=datetime.date(2026, 8, 1))
        assert mult == pytest.approx(bankroll.AHEAD_PACE_MULTIPLIER)


class TestEffectiveCeiling:
    def test_applies_multiplier_to_raw_ceiling(self):
        state = {"ceiling": 100.0}
        result = bankroll.effective_ceiling(state, current_equity=9000, today=datetime.date(2026, 9, 1))
        assert result == pytest.approx(100.0 * bankroll.BEHIND_PACE_MULTIPLIER)

    def test_never_exceeds_max_ceiling(self):
        state = {"ceiling": bankroll.MAX_CEILING}
        # even with the max endgame+behind-pace boost, stays capped
        result = bankroll.effective_ceiling(
            state, current_equity=1, today=bankroll.COMPETITION_END)
        assert result <= bankroll.MAX_CEILING

    def test_neutral_conditions_return_raw_ceiling(self):
        state = {"ceiling": 200.0}
        result = bankroll.effective_ceiling(state, current_equity=10000, today=datetime.date(2026, 9, 1))
        assert result == pytest.approx(200.0)
