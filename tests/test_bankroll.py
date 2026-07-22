#!/usr/bin/env python3
"""
Unit tests for the self-calibrating bankroll ceiling in bankroll.py.

Covers read/write round-trip, win/loss ceiling adjustment, floor/max caps,
growth-rate acceleration/deceleration based on win rate, target-profit-pct
shrinkage above $500 ceiling, and history log formatting/capping.

BANKROLL_FILE is monkeypatched to a tmp_path file in every test — this file
is live production state and must never be touched by tests.
"""
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
        state["decay_rate"] = 0.015
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
        assert reread["decay_rate"] == 0.015
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
