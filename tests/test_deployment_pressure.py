#!/usr/bin/env python3
"""Unit tests for deployment_pressure.py — tracks sustained under-deployment
and turns it into a dynamic conviction floor + research-effort escalation.
Mirrors tests/test_bankroll.py's conventions (state file monkeypatched to
tmp_path, class-per-function)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import deployment_pressure as dp  # noqa: E402


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "deployment_pressure.json"
    monkeypatch.setattr(dp, "STATE_FILE", path)
    return path


class TestReadStateDefaults:
    def test_no_file_returns_zeroed_defaults(self, state_file):
        state = dp.read_state()
        assert state["consecutive_under_deployed_ticks"] == 0
        assert state["last_cash_pct"] == 0.0
        assert state["last_freeform_escalation_ts"] is None
        assert state["last_backtest_escalation_ts"] is None

    def test_corrupt_file_falls_back_to_defaults(self, state_file):
        state_file.write_text("not valid json{{{")
        state = dp.read_state()
        assert state["consecutive_under_deployed_ticks"] == 0


# 2026-07-27T10:00:00Z is bucket-aligned for any interval that divides
# evenly into an hour (300s does: 36000s since midnight / 300 = 120).
_TICK_BASE = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)


def _tick(n, interval_seconds=dp.DEFAULT_TICK_INTERVAL_SECONDS):
    """A timestamp landing in the n-th distinct real tick bucket."""
    return _TICK_BASE + timedelta(seconds=n * interval_seconds)


def _within_same_tick(n, offset_seconds, interval_seconds=dp.DEFAULT_TICK_INTERVAL_SECONDS):
    """A timestamp still inside the n-th tick's bucket, offset_seconds after
    its start -- must be < interval_seconds to stay in the same bucket."""
    assert offset_seconds < interval_seconds
    return _tick(n, interval_seconds) + timedelta(seconds=offset_seconds)


class TestRecordTick:
    def test_increments_when_over_threshold(self, state_file):
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(0))
        dp.record_tick(cash_pct=75.0, threshold_pct=70.0, now=_tick(1))
        state = dp.record_tick(cash_pct=90.0, threshold_pct=70.0, now=_tick(2))
        assert state["consecutive_under_deployed_ticks"] == 3

    def test_resets_when_under_threshold(self, state_file):
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(0))
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(1))
        state = dp.record_tick(cash_pct=50.0, threshold_pct=70.0, now=_tick(2))
        assert state["consecutive_under_deployed_ticks"] == 0

    def test_exactly_at_threshold_counts_as_under_deployed(self, state_file):
        state = dp.record_tick(cash_pct=70.0, threshold_pct=70.0)
        assert state["consecutive_under_deployed_ticks"] == 1

    def test_round_trips_through_write_read(self, state_file):
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0)
        reread = dp.read_state()
        assert reread["consecutive_under_deployed_ticks"] == 1
        assert reread["last_cash_pct"] == 80.0

    def test_call_within_same_tick_does_not_double_count(self, state_file):
        """2026-07-27 bug found live: status (and therefore record_tick) can
        be called more than once within a single real trade tick -- e.g.
        Stan re-checking status mid-tick. The streak inflated to 1481 in
        ~90 real minutes (should have been ~18) under the first (wrong)
        fix, a sliding wall-clock debounce. Ticks are identified by which
        schedule-aligned bucket they fall in, not elapsed time -- repeated
        calls anywhere within the same tick's bucket must count as ONE."""
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(0))
        for offset in (10, 100, 250):  # all still within tick 0's bucket
            state = dp.record_tick(cash_pct=80.0, threshold_pct=70.0,
                                    now=_within_same_tick(0, offset))
        assert state["consecutive_under_deployed_ticks"] == 1

    def test_same_tick_refresh_still_updates_cash_pct(self, state_file):
        """Same-bucket calls skip incrementing the streak, but a later
        reading within that same tick should still be reflected."""
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(0))
        state = dp.record_tick(cash_pct=85.0, threshold_pct=70.0, now=_within_same_tick(0, 30))
        assert state["consecutive_under_deployed_ticks"] == 1  # unchanged, still same tick
        assert state["last_cash_pct"] == 85.0  # but the reading itself is fresh

    def test_next_bucket_counts_as_new_tick_even_if_called_seconds_after_a_slow_prior_tick(self, state_file):
        """A tick session that runs long (spills close to the next bucket
        boundary) must not suppress the next real tick's count -- bucket
        identity, not 'how long since the last call', is what matters."""
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_within_same_tick(0, 295))  # tick 0, very late in its bucket
        state = dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(1))  # tick 1 starts moments later
        assert state["consecutive_under_deployed_ticks"] == 2

    def test_custom_interval_seconds_respected(self, state_file):
        """executor.py passes the real params.json tick.interval_seconds
        through -- confirm a non-default interval changes bucket sizing."""
        interval = 60
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(0, interval), interval_seconds=interval)
        same_bucket = dp.record_tick(cash_pct=80.0, threshold_pct=70.0,
                                      now=_within_same_tick(0, 30, interval), interval_seconds=interval)
        assert same_bucket["consecutive_under_deployed_ticks"] == 1
        next_bucket = dp.record_tick(cash_pct=80.0, threshold_pct=70.0,
                                      now=_tick(1, interval), interval_seconds=interval)
        assert next_bucket["consecutive_under_deployed_ticks"] == 2


class TestReset:
    def test_reset_zeroes_streak(self, state_file):
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(0))
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(1))
        state = dp.reset()
        assert state["consecutive_under_deployed_ticks"] == 0

    def test_reset_persists(self, state_file):
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0)
        dp.reset()
        assert dp.read_state()["consecutive_under_deployed_ticks"] == 0

    def test_next_tick_after_reset_recomputes_fresh(self, state_file):
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(0))
        dp.reset(now=_tick(0))
        state = dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(1))
        assert state["consecutive_under_deployed_ticks"] == 1

    def test_reset_does_not_get_reinflated_by_a_stray_same_tick_status_call(self, state_file):
        """A BUY resets mid-tick; if something calls status again later in
        that SAME tick, it must not re-increment back to 1."""
        dp.record_tick(cash_pct=95.0, threshold_pct=70.0, now=_tick(0))
        dp.reset(now=_within_same_tick(0, 50))
        state = dp.record_tick(cash_pct=90.0, threshold_pct=70.0, now=_within_same_tick(0, 100))
        assert state["consecutive_under_deployed_ticks"] == 0


class TestConvictionFloor:
    def test_flat_at_base_through_grace_period(self):
        assert dp.conviction_floor(0.50, 0.35, consecutive_ticks=0) == 0.50
        assert dp.conviction_floor(0.50, 0.35, consecutive_ticks=dp.GRACE_TICKS) == 0.50

    def test_ramps_down_after_grace_period(self):
        mid = dp.conviction_floor(0.50, 0.35, consecutive_ticks=dp.GRACE_TICKS + dp.RAMP_TICKS // 2)
        assert 0.35 < mid < 0.50

    def test_reaches_floor_min_at_full_ramp(self):
        assert dp.conviction_floor(0.50, 0.35, consecutive_ticks=dp.GRACE_TICKS + dp.RAMP_TICKS) == pytest.approx(0.35)

    def test_never_drops_below_floor_min_for_arbitrarily_long_streak(self):
        assert dp.conviction_floor(0.50, 0.35, consecutive_ticks=100_000) == pytest.approx(0.35)

    def test_monotonically_non_increasing_with_more_ticks(self):
        vals = [dp.conviction_floor(0.50, 0.35, t) for t in range(0, dp.GRACE_TICKS + dp.RAMP_TICKS + 20, 5)]
        assert all(a >= b for a, b in zip(vals, vals[1:]))


class TestResearchEscalation:
    def test_no_escalation_below_thresholds(self):
        state = {"consecutive_under_deployed_ticks": 5}
        result = dp.research_escalation(state)
        assert result == {"escalate_freeform_research": False, "escalate_backtest_check": False}

    def test_freeform_escalates_past_threshold(self):
        state = {"consecutive_under_deployed_ticks": dp.FREEFORM_ESCALATION_TICKS}
        result = dp.research_escalation(state)
        assert result["escalate_freeform_research"] is True
        assert result["escalate_backtest_check"] is False

    def test_backtest_escalates_past_its_threshold(self):
        state = {"consecutive_under_deployed_ticks": dp.BACKTEST_ESCALATION_TICKS}
        result = dp.research_escalation(state)
        assert result["escalate_freeform_research"] is True
        assert result["escalate_backtest_check"] is True

    def test_freeform_cooldown_blocks_immediate_refire(self):
        now = datetime.now(timezone.utc)
        state = {
            "consecutive_under_deployed_ticks": dp.FREEFORM_ESCALATION_TICKS,
            "last_freeform_escalation_ts": now.isoformat(),
        }
        result = dp.research_escalation(state, now=now + timedelta(minutes=5))
        assert result["escalate_freeform_research"] is False

    def test_freeform_fires_again_after_cooldown_elapses(self):
        now = datetime.now(timezone.utc)
        state = {
            "consecutive_under_deployed_ticks": dp.FREEFORM_ESCALATION_TICKS,
            "last_freeform_escalation_ts": now.isoformat(),
        }
        later = now + timedelta(hours=dp.FREEFORM_ESCALATION_COOLDOWN_HOURS + 1)
        result = dp.research_escalation(state, now=later)
        assert result["escalate_freeform_research"] is True

    def test_backtest_cooldown_independent_of_freeform(self):
        now = datetime.now(timezone.utc)
        state = {
            "consecutive_under_deployed_ticks": dp.BACKTEST_ESCALATION_TICKS,
            "last_freeform_escalation_ts": now.isoformat(),  # freeform on cooldown
            "last_backtest_escalation_ts": None,             # backtest never fired
        }
        result = dp.research_escalation(state, now=now + timedelta(minutes=5))
        assert result["escalate_freeform_research"] is False
        assert result["escalate_backtest_check"] is True


class TestMarkEscalated:
    def test_mark_freeform_sets_timestamp(self, state_file):
        dp.mark_escalated("freeform")
        state = dp.read_state()
        assert state["last_freeform_escalation_ts"] is not None
        assert state["last_backtest_escalation_ts"] is None

    def test_mark_backtest_sets_timestamp(self, state_file):
        dp.mark_escalated("backtest")
        state = dp.read_state()
        assert state["last_backtest_escalation_ts"] is not None

    def test_mark_does_not_touch_tick_counter(self, state_file):
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(0))
        dp.record_tick(cash_pct=80.0, threshold_pct=70.0, now=_tick(1))
        dp.mark_escalated("freeform")
        assert dp.read_state()["consecutive_under_deployed_ticks"] == 2

    def test_invalid_kind_raises(self, state_file):
        with pytest.raises(AssertionError):
            dp.mark_escalated("nonsense")
