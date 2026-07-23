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
        # 2026-07-23: read_bankroll strips the "-- " on-disk prefix so it
        # doesn't double up on repeated read/write cycles (was a real bug).
        assert reread["history"] == ["07/22 10:00 WIN $+5.00 → $50.00"]


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


class TestLifetimeCountersSurviveReset:
    """2026-07-23: lifetime_trades/lifetime_net_pnl back the unlock-tier system
    and must NOT be cleared by --reset -- only the session/ceiling counters
    are reset. Discovered this distinction after bankroll.md's session
    counters had actually been reset mid-day, which would have silently
    wiped tier progress if tier_status() read the session fields instead."""

    def test_recalc_ceiling_increments_lifetime_counters(self, bankroll_file):
        state = bankroll.read_bankroll()
        bankroll.recalc_ceiling(state, pnl=5.0, is_win=True)
        bankroll.recalc_ceiling(state, pnl=-2.0, is_win=False)
        assert state["lifetime_trades"] == 2
        assert state["lifetime_net_pnl"] == pytest.approx(3.0)

    def test_lifetime_counters_round_trip_through_write_read(self, bankroll_file):
        state = bankroll.read_bankroll()
        bankroll.recalc_ceiling(state, pnl=5.0, is_win=True)
        bankroll.write_bankroll(state)
        reread = bankroll.read_bankroll()
        assert reread["lifetime_trades"] == 1
        assert reread["lifetime_net_pnl"] == pytest.approx(5.0)

    def test_reset_preserves_lifetime_counters(self, bankroll_file):
        state = bankroll.read_bankroll()
        bankroll.recalc_ceiling(state, pnl=5.0, is_win=True)
        bankroll.recalc_ceiling(state, pnl=5.0, is_win=True)
        bankroll.write_bankroll(state)

        # simulate main()'s --reset handler
        reset_state = bankroll.read_bankroll()
        reset_state["ceiling"] = bankroll.STARTING_CEILING
        reset_state["closed_trades"] = 0
        reset_state["wins"] = 0
        reset_state["losses"] = 0
        reset_state["net_pnl"] = 0.0
        reset_state["history"] = ["reset to defaults"]
        bankroll.write_bankroll(reset_state)

        reread = bankroll.read_bankroll()
        assert reread["closed_trades"] == 0
        assert reread["lifetime_trades"] == 2
        assert reread["lifetime_net_pnl"] == pytest.approx(10.0)


class TestHistoryRoundTripDoesNotDoublePrefix:
    """2026-07-23: read_bankroll() used to store history entries WITH their
    '-- ' prefix, and write_bankroll() always re-added one -- every
    read-modify-write cycle doubled the prefix on pre-existing entries
    ('-- entry' -> '-- -- entry' -> ...). Found via bankroll.md showing
    '-- -- -- -- reset to defaults' after routine use."""

    def test_prefix_does_not_accumulate_across_multiple_writes(self, bankroll_file):
        state = bankroll.read_bankroll()
        bankroll.recalc_ceiling(state, pnl=1.0, is_win=True)
        bankroll.write_bankroll(state)

        for _ in range(3):
            state = bankroll.read_bankroll()
            bankroll.write_bankroll(state)

        text = bankroll_file.read_text()
        for line in text.splitlines():
            if "WIN" in line or "LOSS" in line:
                assert not line.strip().startswith("-- -- ")


class TestTierStatus:
    def test_zero_trades_is_tier_one(self):
        state = {"lifetime_trades": 0, "lifetime_net_pnl": 0.0}
        status = bankroll.tier_status(state)
        assert status["tier"] == 1
        assert status["tier_name"] == "Stocks"
        assert status["next_tier"] == "Shorting"
        assert status["trades_to_next"] == 30

    def test_below_threshold_with_positive_expectancy_stays_tier_one(self):
        state = {"lifetime_trades": 17, "lifetime_net_pnl": 34.0}
        status = bankroll.tier_status(state)
        assert status["tier"] == 1
        assert status["expectancy"] == pytest.approx(2.0)
        assert status["trades_to_next"] == 13
        assert status["expectancy_positive"] is True

    def test_enough_trades_but_negative_expectancy_stays_tier_one(self):
        state = {"lifetime_trades": 35, "lifetime_net_pnl": -20.0}
        status = bankroll.tier_status(state)
        assert status["tier"] == 1
        assert status["trades_to_next"] == 0
        assert status["expectancy_positive"] is False

    def test_enough_trades_and_positive_expectancy_unlocks_tier_two(self):
        state = {"lifetime_trades": 30, "lifetime_net_pnl": 15.0}
        status = bankroll.tier_status(state)
        assert status["tier"] == 2
        assert status["tier_name"] == "Shorting"
        assert status["next_tier"] == "Crypto"
        assert status["trades_to_next"] == 30

    def test_max_tier_has_no_next_tier(self):
        state = {"lifetime_trades": 90, "lifetime_net_pnl": 100.0}
        status = bankroll.tier_status(state)
        assert status["tier"] == 4
        assert status["tier_name"] == "Options / leveraged ETFs / forex"
        assert "next_tier" not in status

    def test_zero_trades_does_not_crash_on_expectancy(self):
        state = {"lifetime_trades": 0, "lifetime_net_pnl": 0.0}
        status = bankroll.tier_status(state)
        assert status["expectancy"] == 0.0

    def test_missing_lifetime_fields_default_to_zero(self):
        # older bankroll.md files predating this feature won't have these keys
        status = bankroll.tier_status({})
        assert status["tier"] == 1
        assert status["trades"] == 0


class TestFormatTier:
    def test_includes_blockers_when_both_present(self):
        status = bankroll.tier_status({"lifetime_trades": 17, "lifetime_net_pnl": -5.0})
        text = bankroll.format_tier(status)
        assert "more trades" in text
        assert "needs positive expectancy" in text

    def test_ready_when_no_blockers_remain(self):
        status = {
            "tier": 1, "tier_name": "Stocks", "trades": 30, "expectancy": 2.0,
            "next_tier": "Shorting", "trades_to_next": 0, "expectancy_positive": True,
        }
        text = bankroll.format_tier(status)
        assert "ready" in text

    def test_max_tier_reached_message(self):
        status = {"tier": 4, "tier_name": "Options / leveraged ETFs / forex", "trades": 100, "expectancy": 5.0}
        text = bankroll.format_tier(status)
        assert "Max tier reached" in text
