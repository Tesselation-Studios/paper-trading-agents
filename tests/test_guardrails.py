#!/usr/bin/env python3
"""
Unit tests for the trade guardrail gates in scripts/executor.py.

Covers each gate's independent edge cases plus the GuardrailEngine-style
chain in check_order() (toggles, fail-open on missing data, first-rejection
stops the chain). No network calls — get_account/get_positions/place_order
are monkeypatched, matching paper-trading-rebuild's tests/test_risk.py
convention (see that repo for the pattern this mirrors).
"""
import datetime
import json
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import executor  # noqa: E402


# A fixed Wednesday 12:00 ET during market hours, for gates that don't care
# about hours but would otherwise flake depending on when tests run.
MARKET_OPEN_TS = datetime.datetime(2026, 7, 22, 12, 0, tzinfo=ZoneInfo("America/New_York"))


DEFAULT_PARAMS = {
    "risk": {"max_position_pct": 6.0, "max_positions": 25, "conviction_floor": 0.5,
              "duplicate_order_cooldown_seconds": 60},
    "risk_guards": {"max_positions_per_sector": 2, "order_count_audit_threshold_daily": 10},
    "guardrail_gates": {
        "cash": True, "position_size": True, "max_positions": True,
        "sector_concentration": True, "hours": True, "conviction": True,
        "bankroll": True, "hard_stop": True, "trailing_stop": True,
        "position_size_trim": True, "duplicate_order": True, "order_count_audit": True,
    },
}


@pytest.fixture
def params(monkeypatch):
    """Default params.json content, patched in per-test so tests don't
    depend on the real file (or each other) and are order-independent."""
    data = json.loads(json.dumps(DEFAULT_PARAMS))  # deep copy
    monkeypatch.setattr(executor, "load_params", lambda: data)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# gate_cash
# ─────────────────────────────────────────────────────────────────────────────


class TestGateCash:
    def test_buy_within_cash(self):
        context = {"cash": 50000}
        action = {"action": "BUY", "quantity": 10, "price": 100.0}
        granted, reason = executor.gate_cash(context, action)
        assert granted is True
        assert "sufficient" in reason

    def test_buy_exceeds_cash(self):
        context = {"cash": 100}
        action = {"action": "BUY", "quantity": 10, "price": 100.0}
        granted, reason = executor.gate_cash(context, action)
        assert granted is False
        assert "$1,000.00" in reason and "$100.00" in reason

    def test_buy_exact_cash(self):
        context = {"cash": 1000}
        action = {"action": "BUY", "quantity": 10, "price": 100.0}
        granted, _ = executor.gate_cash(context, action)
        assert granted is True

    def test_sell_always_allowed(self):
        context = {"cash": 0}
        action = {"action": "SELL", "quantity": 10, "price": 100.0}
        granted, reason = executor.gate_cash(context, action)
        assert granted is True
        assert "non-BUY" in reason

    def test_no_price_fails_open(self):
        context = {"cash": 0}
        action = {"action": "BUY", "quantity": 10, "price": None}
        granted, reason = executor.gate_cash(context, action)
        assert granted is True
        assert "fail-open" in reason


# ─────────────────────────────────────────────────────────────────────────────
# gate_position_size
# ─────────────────────────────────────────────────────────────────────────────


class TestGatePositionSize:
    def test_new_position_within_cap(self, params):
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "SOFI", "quantity": 10, "price": 4.0}
        granted, reason = executor.gate_position_size(context, action)
        assert granted is True
        assert "0.4%" in reason

    def test_new_position_exceeds_cap(self, params):
        # 10000 * 6% = $600 cap; this buy alone is $1000
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "NVDA", "quantity": 5, "price": 200.0}
        granted, reason = executor.gate_position_size(context, action)
        assert granted is False
        assert "exceeds 6% cap" in reason

    def test_existing_position_pushes_over_cap(self, params):
        # NVDA already at $950 (9.5%), adding $100 more pushes to 10.5% > 6%
        context = {
            "portfolio_value": 10000,
            "positions": [{"symbol": "NVDA", "market_value": 950.0}],
        }
        action = {"action": "BUY", "ticker": "NVDA", "quantity": 1, "price": 100.0}
        granted, reason = executor.gate_position_size(context, action)
        assert granted is False
        assert "existing $950.00" in reason

    def test_no_portfolio_value_fails_open(self, params):
        context = {"portfolio_value": 0, "positions": []}
        action = {"action": "BUY", "ticker": "NVDA", "quantity": 1, "price": 100.0}
        granted, reason = executor.gate_position_size(context, action)
        assert granted is True
        assert "fail-open" in reason

    def test_sell_skips(self, params):
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "SELL", "ticker": "NVDA", "quantity": 100, "price": 100.0}
        granted, _ = executor.gate_position_size(context, action)
        assert granted is True


# ─────────────────────────────────────────────────────────────────────────────
# gate_max_positions
# ─────────────────────────────────────────────────────────────────────────────


class TestGateMaxPositions:
    def test_room_available(self, params):
        context = {"positions": [{"symbol": "A"}, {"symbol": "B"}]}
        action = {"action": "BUY", "ticker": "C"}
        granted, _ = executor.gate_max_positions(context, action)
        assert granted is True

    def test_at_cap_blocks_new_ticker(self, params):
        params["risk"]["max_positions"] = 2
        context = {"positions": [{"symbol": "A"}, {"symbol": "B"}]}
        action = {"action": "BUY", "ticker": "C"}
        granted, reason = executor.gate_max_positions(context, action)
        assert granted is False
        assert "2/2" in reason

    def test_at_cap_allows_adding_to_existing(self, params):
        params["risk"]["max_positions"] = 2
        context = {"positions": [{"symbol": "A"}, {"symbol": "B"}]}
        action = {"action": "BUY", "ticker": "A"}
        granted, reason = executor.gate_max_positions(context, action)
        assert granted is True
        assert "existing" in reason


# ─────────────────────────────────────────────────────────────────────────────
# gate_sector_concentration
# ─────────────────────────────────────────────────────────────────────────────


class TestGateSectorConcentration:
    def test_no_sector_data_fails_open(self, params):
        context = {"positions": []}
        action = {"action": "BUY", "ticker": "XYZ"}
        granted, reason = executor.gate_sector_concentration(context, action)
        assert granted is True
        assert "fail-open" in reason

    def test_sector_passed_explicitly_within_cap(self, params, monkeypatch):
        monkeypatch.setattr(executor, "_sector_of", lambda t: None)
        context = {"positions": []}
        action = {"action": "BUY", "ticker": "XYZ", "sector": "Tech"}
        granted, reason = executor.gate_sector_concentration(context, action)
        assert granted is True
        assert "0/2" in reason

    def test_sector_at_cap_blocks(self, params, monkeypatch):
        # Two existing positions both in "Tech" per _sector_of, cap is 2
        monkeypatch.setattr(executor, "_sector_of", lambda t: "Tech" if t in ("AAA", "BBB") else None)
        context = {"positions": [{"symbol": "AAA"}, {"symbol": "BBB"}]}
        action = {"action": "BUY", "ticker": "CCC", "sector": "Tech"}
        granted, reason = executor.gate_sector_concentration(context, action)
        assert granted is False
        assert "at 2 cap" in reason


# ─────────────────────────────────────────────────────────────────────────────
# gate_hours (timestamp injected via context["_test_now"] — see executor.py)
# ─────────────────────────────────────────────────────────────────────────────


class TestGateHours:
    def test_within_market_hours(self):
        context = {"_test_now": MARKET_OPEN_TS}
        granted, reason = executor.gate_hours(context, {})
        assert granted is True
        assert "market open" in reason

    def test_before_open(self):
        ts = MARKET_OPEN_TS.replace(hour=9, minute=0)
        granted, reason = executor.gate_hours({"_test_now": ts}, {})
        assert granted is False
        assert "market open 09:30-16:00 ET" in reason

    def test_after_close(self):
        ts = MARKET_OPEN_TS.replace(hour=16, minute=1)
        granted, reason = executor.gate_hours({"_test_now": ts}, {})
        assert granted is False

    def test_at_exact_open_boundary(self):
        ts = MARKET_OPEN_TS.replace(hour=9, minute=30, second=0)
        granted, _ = executor.gate_hours({"_test_now": ts}, {})
        assert granted is True

    def test_weekend_blocked(self):
        saturday = datetime.datetime(2026, 7, 25, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        granted, reason = executor.gate_hours({"_test_now": saturday}, {})
        assert granted is False
        assert "Saturday" in reason


# ─────────────────────────────────────────────────────────────────────────────
# gate_conviction
# ─────────────────────────────────────────────────────────────────────────────


class TestGateConviction:
    def test_above_floor(self, params):
        granted, _ = executor.gate_conviction({}, {"action": "BUY", "conviction": 0.7})
        assert granted is True

    def test_below_floor(self, params):
        granted, reason = executor.gate_conviction({}, {"action": "BUY", "conviction": 0.3})
        assert granted is False
        assert "below 0.50 floor" in reason

    def test_no_conviction_fails_open(self, params):
        granted, reason = executor.gate_conviction({}, {"action": "BUY"})
        assert granted is True
        assert "fail-open" in reason

    def test_exactly_at_floor_passes(self, params):
        granted, _ = executor.gate_conviction({}, {"action": "BUY", "conviction": 0.5})
        assert granted is True


# ─────────────────────────────────────────────────────────────────────────────
# gate_bankroll
# ─────────────────────────────────────────────────────────────────────────────


class TestGateBankroll:
    def test_within_ceiling(self, params, monkeypatch):
        fake_bankroll = type("M", (), {"read_bankroll": staticmethod(lambda: {"ceiling": 50.0})})
        monkeypatch.setitem(sys.modules, "bankroll", fake_bankroll)
        granted, reason = executor.gate_bankroll({}, {"action": "BUY", "quantity": 5, "price": 5.0})
        assert granted is True
        assert "within bankroll ceiling" in reason

    def test_exceeds_ceiling(self, params, monkeypatch):
        fake_bankroll = type("M", (), {"read_bankroll": staticmethod(lambda: {"ceiling": 50.0})})
        monkeypatch.setitem(sys.modules, "bankroll", fake_bankroll)
        granted, reason = executor.gate_bankroll({}, {"action": "BUY", "quantity": 100, "price": 5.0})
        assert granted is False
        assert "exceeds bankroll ceiling" in reason

    def test_no_portfolio_value_in_context_falls_back_to_raw_ceiling(self, params, monkeypatch):
        """2026-07-23: competition-mode adjustment needs real portfolio_value
        to compute — context={} (as used elsewhere in this test class) must
        still work, fail-open to the raw ceiling, not crash."""
        fake_bankroll = type("M", (), {"read_bankroll": staticmethod(lambda: {"ceiling": 50.0})})
        monkeypatch.setitem(sys.modules, "bankroll", fake_bankroll)
        granted, reason = executor.gate_bankroll({}, {"action": "BUY", "quantity": 5, "price": 5.0})
        assert granted is True

    def test_uses_effective_ceiling_when_portfolio_value_present(self, params, monkeypatch):
        """When real portfolio_value IS available, gate_bankroll must call
        bankroll.effective_ceiling(state, portfolio_value) -- not just the
        raw state['ceiling'] -- so competition-mode adjustment actually
        takes effect."""
        calls = []

        def fake_effective_ceiling(state, equity):
            calls.append((state["ceiling"], equity))
            return 200.0  # competition-boosted, higher than raw $50

        fake_bankroll = type("M", (), {
            "read_bankroll": staticmethod(lambda: {"ceiling": 50.0}),
            "effective_ceiling": staticmethod(fake_effective_ceiling),
        })
        monkeypatch.setitem(sys.modules, "bankroll", fake_bankroll)

        # Cost ($150) exceeds the raw $50 ceiling but not the boosted $200 --
        # confirms the boosted value is actually what's compared against.
        granted, reason = executor.gate_bankroll(
            {"portfolio_value": 9000.0}, {"action": "BUY", "quantity": 30, "price": 5.0})
        assert granted is True
        assert "$200.00" in reason
        assert calls == [(50.0, 9000.0)]


# ─────────────────────────────────────────────────────────────────────────────
# gate_duplicate_order — added 2026-07-22 after DVN got bought 3x in one tick
# (three separate BUY orders, 5-7 seconds apart — nothing stopped a repeat
# submission the agent didn't realize had already executed)
# ─────────────────────────────────────────────────────────────────────────────


class TestGateDuplicateOrder:
    @pytest.fixture(autouse=True)
    def isolated_state(self, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "RECENT_ORDERS_PATH", tmp_path / "recent_orders.json")
        # record_order_submitted() also bumps the daily-order-count file —
        # DAILY_ORDER_COUNT_PATH must be patched explicitly too, not implied
        # by patching STATE_DIR below (it was already bound to the real
        # path at module-import time). Missing this leaked real writes from
        # every test run into the production state/daily_order_count.json,
        # confirmed 2026-07-23 — Stan hadn't placed a single real order that
        # day, yet the file showed count=15 purely from repeated pytest runs.
        monkeypatch.setattr(executor, "DAILY_ORDER_COUNT_PATH", tmp_path / "daily_order_count.json")
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)

    def test_no_prior_order_passes(self, params):
        granted, reason = executor.gate_duplicate_order({}, {"action": "BUY", "ticker": "DVN"})
        assert granted is True
        assert "no recent matching" in reason

    def test_immediate_repeat_blocked(self, params):
        executor.record_order_submitted("DVN", "BUY")
        granted, reason = executor.gate_duplicate_order({}, {"action": "BUY", "ticker": "DVN"})
        assert granted is False
        assert "duplicate" in reason

    def test_different_ticker_not_blocked(self, params):
        executor.record_order_submitted("DVN", "BUY")
        granted, _ = executor.gate_duplicate_order({}, {"action": "BUY", "ticker": "WSC"})
        assert granted is True

    def test_different_action_same_ticker_not_blocked(self, params):
        """A BUY followed immediately by a SELL of the same ticker is a real
        scenario (e.g. a stop breach right after entry) — must not be
        confused with a duplicate of the same action."""
        executor.record_order_submitted("DVN", "BUY")
        granted, _ = executor.gate_duplicate_order({}, {"action": "SELL", "ticker": "DVN"})
        assert granted is True

    def test_applies_to_sell_too(self, params):
        executor.record_order_submitted("SOFI", "SELL")
        granted, reason = executor.gate_duplicate_order({}, {"action": "SELL", "ticker": "SOFI"})
        assert granted is False

    def test_outside_cooldown_window_passes(self, params):
        # Write a timestamp 61s in the past directly, rather than mocking the
        # global time module (which record_order_submitted itself also uses).
        executor.RECENT_ORDERS_PATH.write_text(json.dumps({"DVN:BUY": time.time() - 61}))
        granted, reason = executor.gate_duplicate_order({}, {"action": "BUY", "ticker": "DVN"})
        assert granted is True
        assert "outside" in reason

    def test_missing_ticker_fails_open(self, params):
        granted, reason = executor.gate_duplicate_order({}, {"action": "BUY"})
        assert granted is True

    def test_record_then_check_full_cycle(self, params):
        """End-to-end: three rapid BUYs of the same ticker, only the first
        should have been allowed by a real caller checking between each."""
        assert executor.gate_duplicate_order({}, {"action": "BUY", "ticker": "DVN"})[0] is True
        executor.record_order_submitted("DVN", "BUY")
        assert executor.gate_duplicate_order({}, {"action": "BUY", "ticker": "DVN"})[0] is False
        assert executor.gate_duplicate_order({}, {"action": "BUY", "ticker": "DVN"})[0] is False


class TestGateDailyOrderCount:
    """2026-07-23: strategy.md promised a daily order-count audit
    (risk_guards) since v1.0 that nothing ever mechanically enforced —
    found dead by workspace_review.py. Mirrors TestGateDuplicateOrder's
    conventions closely (same isolated-state fixture shape, same
    fail-open/toggle expectations)."""

    TODAY = "2026-07-23"

    @pytest.fixture(autouse=True)
    def isolated_state(self, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "DAILY_ORDER_COUNT_PATH", tmp_path / "daily_order_count.json")
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)

    def test_zero_orders_today_passes(self, params):
        granted, reason = executor.gate_daily_order_count({}, {"action": "BUY", "ticker": "DVN"})
        assert granted is True
        assert "0/10" in reason

    def test_under_threshold_passes(self, params):
        for _ in range(5):
            executor.record_order_submitted("DVN", "BUY", today=self.TODAY)
        granted, reason = executor.gate_daily_order_count(
            {"_test_now": datetime.datetime.fromisoformat(self.TODAY)}, {"action": "BUY", "ticker": "WSC"})
        assert granted is True
        assert "5/10" in reason

    def test_at_threshold_blocks(self, params):
        for _ in range(10):
            executor.record_order_submitted("DVN", "BUY", today=self.TODAY)
        granted, reason = executor.gate_daily_order_count(
            {"_test_now": datetime.datetime.fromisoformat(self.TODAY)}, {"action": "SELL", "ticker": "WSC"})
        assert granted is False
        assert "rogue loop" in reason

    def test_applies_to_sell_too(self, params):
        for _ in range(10):
            executor.record_order_submitted("DVN", "SELL", today=self.TODAY)
        granted, _ = executor.gate_daily_order_count(
            {"_test_now": datetime.datetime.fromisoformat(self.TODAY)}, {"action": "SELL", "ticker": "WSC"})
        assert granted is False

    def test_counts_across_different_tickers_and_actions(self, params):
        """Unlike gate_duplicate_order, this isn't per-ticker — a rogue
        loop hammering many different tickers is exactly the failure mode
        it exists to catch."""
        executor.record_order_submitted("DVN", "BUY", today=self.TODAY)
        executor.record_order_submitted("WSC", "SELL", today=self.TODAY)
        executor.record_order_submitted("GME", "BUY", today=self.TODAY)
        granted, reason = executor.gate_daily_order_count(
            {"_test_now": datetime.datetime.fromisoformat(self.TODAY)}, {"action": "BUY", "ticker": "SNAP"})
        assert granted is True
        assert "3/10" in reason

    def test_resets_on_new_day(self, params):
        for _ in range(10):
            executor.record_order_submitted("DVN", "BUY", today=self.TODAY)
        granted, reason = executor.gate_daily_order_count(
            {"_test_now": datetime.datetime.fromisoformat("2026-07-24")}, {"action": "BUY", "ticker": "WSC"})
        assert granted is True
        assert "0/10" in reason

    def test_missing_action_fails_open(self, params):
        granted, reason = executor.gate_daily_order_count({}, {"ticker": "DVN"})
        assert granted is True
        assert "non-BUY/SELL" in reason

    def test_disabled_via_toggle_skips_in_check_order_chain(self, params, monkeypatch):
        params["guardrail_gates"]["order_count_audit"] = False
        for _ in range(10):
            executor.record_order_submitted("DVN", "BUY", today=self.TODAY)
        monkeypatch.setattr(executor, "GATES", {"order_count_audit": executor.gate_daily_order_count})
        monkeypatch.setattr(executor, "get_account", lambda account: {"cash": "100000", "portfolio_value": "100000"})
        monkeypatch.setattr(executor, "get_positions", lambda account: [])
        granted, reason, results = executor.check_order(
            "stonks", "BUY", "WSC", 1, price=10.0,
            conviction=0.9, sector="Tech")
        assert granted is True
        assert results[0]["reason"] == "disabled via params.json guardrail_gates"


# ─────────────────────────────────────────────────────────────────────────────
# check_order — full chain: toggles, fail-open behavior, first-rejection stops
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckOrderChain:
    @pytest.fixture
    def mock_account(self, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "get_account", lambda account: {"equity": "10000", "cash": "8000"})
        monkeypatch.setattr(executor, "get_positions", lambda account: [])
        # Isolate from any real state/*.json so gate_duplicate_order and
        # gate_daily_order_count don't depend on filesystem state left over
        # from real trading (confirmed 2026-07-23: Stan had genuinely
        # placed 10 real orders today, tripping gate_daily_order_count for
        # real against these tests' un-isolated state).
        monkeypatch.setattr(executor, "RECENT_ORDERS_PATH", tmp_path / "recent_orders.json")
        monkeypatch.setattr(executor, "DAILY_ORDER_COUNT_PATH", tmp_path / "daily_order_count.json")

    def test_all_gates_pass(self, params, mock_account, monkeypatch):
        monkeypatch.setitem(executor.GATES, "hours", lambda c, a: (True, "market open"))
        granted, reason, results = executor.check_order(
            "stonks", "BUY", "SOFI", 5, price=4.0, conviction=0.9,
        )
        assert granted is True
        assert reason == "All gates passed"
        assert len(results) == len(executor.GATES)

    def test_disabled_gate_shows_as_disabled_not_evaluated(self, params, mock_account, monkeypatch):
        params["guardrail_gates"]["hours"] = False
        granted, reason, results = executor.check_order("stonks", "BUY", "SOFI", 5, price=4.0, conviction=0.9)
        hours_result = next(r for r in results if r["gate"] == "hours")
        assert hours_result["passed"] is True
        assert "disabled via params.json" in hours_result["reason"]

    def test_first_rejection_stops_chain(self, params, mock_account, monkeypatch):
        # conviction floor 0.5, pass a BUY with conviction 0.1 -> should be
        # rejected by conviction gate; gates after it in dict order shouldn't
        # need to run (chain stops), but gates before it still show results.
        monkeypatch.setitem(executor.GATES, "hours", lambda c, a: (True, "market open"))
        granted, reason, results = executor.check_order(
            "stonks", "BUY", "SOFI", 5, price=4.0, conviction=0.1,
        )
        assert granted is False
        assert "Blocked by conviction" in reason
        assert results[-1]["gate"] == "conviction"
        assert results[-1]["passed"] is False

    def test_gate_exception_fails_open(self, params, mock_account, monkeypatch):
        def broken_gate(context, action):
            raise RuntimeError("boom")
        monkeypatch.setitem(executor.GATES, "cash", broken_gate)
        granted, reason, results = executor.check_order("stonks", "BUY", "SOFI", 5, price=4.0)
        cash_result = next(r for r in results if r["gate"] == "cash")
        assert cash_result["passed"] is True
        assert "ERROR (fail-open)" in cash_result["reason"]

    def test_sell_bypasses_buy_only_gates(self, params, mock_account, monkeypatch):
        monkeypatch.setitem(executor.GATES, "hours", lambda c, a: (True, "market open"))
        granted, reason, results = executor.check_order("stonks", "SELL", "SOFI", 5, price=4.0)
        assert granted is True


# ─────────────────────────────────────────────────────────────────────────────
# check_stops — hard stop / trailing stop breach detection
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckStops:
    def _position(self, symbol, entry, current, qty=1, market_value=None):
        # market_value defaults to qty * current so callers not testing
        # position_size_trim don't need to compute it by hand.
        mv = market_value if market_value is not None else qty * current
        return {
            "symbol": symbol, "avg_entry_price": str(entry), "current_price": str(current),
            "qty": str(qty), "market_value": str(mv),
        }

    def test_no_breach(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -10.0, "trailing_stop_pct": 5.0}
        params["guardrail_gates"]["position_size_trim"] = False  # not under test here
        # -4% on first observation: peak inits to entry_price (10.0), so this
        # must stay clear of the 5% trailing-stop boundary (9.5 exactly
        # breaches via <=) to genuinely exercise the no-breach path.
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("SOFI", 10.0, 9.6)])
        breaches = executor.check_stops("stonks")
        assert breaches == []

    def test_hard_stop_breach(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -10.0, "trailing_stop_pct": 5.0}
        params["guardrail_gates"]["position_size_trim"] = False
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("SOFI", 10.0, 8.5)])
        breaches = executor.check_stops("stonks")
        assert len(breaches) == 1
        assert breaches[0]["ticker"] == "SOFI"
        assert breaches[0]["stop_type"] == "hard"

    def test_trailing_stop_breach_after_peak(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -50.0, "trailing_stop_pct": 5.0}  # wide hard stop, won't trigger
        params["guardrail_gates"]["position_size_trim"] = False
        # Tick 1: price runs up to 15 (new peak), tick 2: drops to 14.2 (>5% off peak 15 -> breach)
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("SOFI", 10.0, 15.0)])
        breaches = executor.check_stops("stonks")
        assert breaches == []  # first observation, no drop yet

        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("SOFI", 10.0, 14.2)])
        breaches = executor.check_stops("stonks")
        assert len(breaches) == 1
        assert breaches[0]["stop_type"] == "trailing"

    def test_disabled_gates_produce_no_breaches(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -10.0, "trailing_stop_pct": 5.0}
        params["guardrail_gates"]["hard_stop"] = False
        params["guardrail_gates"]["trailing_stop"] = False
        params["guardrail_gates"]["position_size_trim"] = False
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("SOFI", 10.0, 5.0)])
        breaches = executor.check_stops("stonks")
        assert breaches == []

    def test_state_cleaned_up_for_closed_positions(self, params, monkeypatch, tmp_path):
        state_path = tmp_path / "guardrail_stops.json"
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", state_path)
        state_path.write_text(json.dumps({"CLOSED": {"peak_price": 10.0, "entry_price": 9.0}}))
        params["risk"] = {"stop_loss_pct": -10.0, "trailing_stop_pct": 5.0}
        params["guardrail_gates"]["position_size_trim"] = False
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("SOFI", 10.0, 9.5)])
        executor.check_stops("stonks")
        saved = json.loads(state_path.read_text())
        assert "CLOSED" not in saved
        assert "SOFI" in saved

    # ── Oversized-position detection — added 2026-07-22 ──────────────────
    # NVDA sat over its 6% cap for 4 days / 3 nightly cycles because
    # gate_position_size only blocks NEW over-cap buys, nothing corrected an
    # existing position that grew past the cap via price appreciation.

    def test_oversized_position_flagged_with_trim_amount(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -50.0, "trailing_stop_pct": 50.0, "max_position_pct": 6.0}
        monkeypatch.setattr(executor, "get_account", lambda a: {"equity": "10000"})
        # 5 shares @ $211.76 = $1058.80 = 10.6% of $10,000 portfolio, cap is 6% ($600)
        monkeypatch.setattr(
            executor, "get_positions",
            lambda a: [self._position("NVDA", entry=207.63, current=211.76, qty=5, market_value=1058.80)],
        )
        breaches = executor.check_stops("stonks")
        oversized = [b for b in breaches if b["stop_type"] == "oversized"]
        assert len(oversized) == 1
        assert oversized[0]["ticker"] == "NVDA"
        assert oversized[0]["shares_to_sell"] >= 1
        # Selling 2 shares brings it to 3 @ $211.76 = $635.28 = 6.35%, still
        # slightly over — 3 shares -> $423.52 = 4.2%, clearly under. Either
        # 2 or 3 is a reasonable trim; assert it's not something absurd like
        # "sell all 5" or "sell 0".
        assert 1 <= oversized[0]["shares_to_sell"] < 5

    def test_within_cap_not_flagged(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -50.0, "trailing_stop_pct": 50.0, "max_position_pct": 6.0}
        monkeypatch.setattr(executor, "get_account", lambda a: {"equity": "10000"})
        # $500 of $10,000 = 5%, within the 6% cap
        monkeypatch.setattr(
            executor, "get_positions",
            lambda a: [self._position("SOFI", entry=10.0, current=10.0, qty=50, market_value=500.0)],
        )
        breaches = executor.check_stops("stonks")
        assert breaches == []

    def test_disabling_position_size_trim_skips_the_check(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -50.0, "trailing_stop_pct": 50.0, "max_position_pct": 6.0}
        params["guardrail_gates"]["position_size_trim"] = False

        def fail_if_called(a):
            raise AssertionError("get_account should not be called when position_size_trim is disabled")
        monkeypatch.setattr(executor, "get_account", fail_if_called)
        monkeypatch.setattr(
            executor, "get_positions",
            lambda a: [self._position("NVDA", entry=207.63, current=211.76, qty=5, market_value=1058.80)],
        )
        breaches = executor.check_stops("stonks")
        assert breaches == []

    def test_oversized_and_hard_stop_can_both_fire_for_different_tickers(self, params, monkeypatch, tmp_path):
        """Oversized is a trim, not an exit — it must not short-circuit the
        loop the way a hard-stop breach does (continue), so other tickers'
        checks still run in the same pass."""
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -10.0, "trailing_stop_pct": 50.0, "max_position_pct": 6.0}
        monkeypatch.setattr(executor, "get_account", lambda a: {"equity": "10000"})
        monkeypatch.setattr(executor, "get_positions", lambda a: [
            self._position("NVDA", entry=207.63, current=211.76, qty=5, market_value=1058.80),  # oversized
            self._position("GME", entry=22.0, current=19.0, qty=10, market_value=190.0),          # hard stop (-13.6%)
        ])
        breaches = executor.check_stops("stonks")
        stop_types = {b["ticker"]: b["stop_type"] for b in breaches}
        assert stop_types.get("NVDA") == "oversized"
        assert stop_types.get("GME") == "hard"


# ─────────────────────────────────────────────────────────────────────────────
# close_trade_outcome — bankroll update + Postgres outcome label on SELL
# ─────────────────────────────────────────────────────────────────────────────


class TestCloseTradeOutcome:
    """Regression coverage for 2026-07-22: outcome labeling used to depend
    on the LLM remembering a separate record_decision.py close call —
    confirmed roughly half of real closed trades that week never got
    labeled. Mechanized into the same SELL codepath that already reliably
    updates bankroll.py's ceiling."""

    @pytest.fixture
    def fake_bankroll_module(self, monkeypatch, tmp_path):
        calls = {}

        class FakeBankroll:
            @staticmethod
            def read_bankroll():
                return {"ceiling": 50.0, "wins": 0, "losses": 0, "closed_trades": 0,
                        "net_pnl": 0.0, "growth_rate": 0.02, "decay_rate": 0.01,
                        "target_profit_pct": 1.0, "history": []}

            @staticmethod
            def recalc_ceiling(state, pnl, is_win):
                calls["recalc"] = {"pnl": pnl, "is_win": is_win}

            @staticmethod
            def write_bankroll(state):
                calls["written"] = True

        monkeypatch.setitem(sys.modules, "bankroll", FakeBankroll)
        return calls

    def test_win_updates_bankroll_and_labels_outcome(self, fake_bankroll_module, monkeypatch):
        recorded = {}

        class FakeDecisions:
            @staticmethod
            def record_trade_close(trader_id, ticker, trade_id, pnl, return_pct):
                recorded.update(trader_id=trader_id, ticker=ticker, pnl=pnl, return_pct=return_pct)
                return {"training_example_id": 1, "labeled": True}

        monkeypatch.setitem(sys.modules, "decisions", FakeDecisions)

        result = executor.close_trade_outcome("stonks", "SOFI", entry_price=10.0, exit_price=11.0, qty=5)

        assert result["pnl"] == pytest.approx(5.0)
        assert result["return_pct"] == pytest.approx(10.0)
        assert result["outcome_label_warning"] is None
        assert fake_bankroll_module["recalc"] == {"pnl": pytest.approx(5.0), "is_win": True}
        assert fake_bankroll_module["written"] is True
        assert recorded == {"trader_id": "stonks", "ticker": "SOFI", "pnl": pytest.approx(5.0), "return_pct": pytest.approx(10.0)}

    def test_loss_marks_is_win_false(self, fake_bankroll_module, monkeypatch):
        class FakeDecisions:
            @staticmethod
            def record_trade_close(**kwargs):
                return {"labeled": True}

        monkeypatch.setitem(sys.modules, "decisions", FakeDecisions)

        executor.close_trade_outcome("stonks", "SOFI", entry_price=10.0, exit_price=9.0, qty=5)
        assert fake_bankroll_module["recalc"]["is_win"] is False

    def test_postgres_failure_does_not_raise(self, fake_bankroll_module, monkeypatch):
        """A labeling failure must not look like a trade failure — the
        order already executed by the time this runs. Bankroll must still
        update even if Postgres is unreachable."""
        class FakeDecisions:
            @staticmethod
            def record_trade_close(**kwargs):
                raise RuntimeError("connection to docker.klo refused")

        monkeypatch.setitem(sys.modules, "decisions", FakeDecisions)

        result = executor.close_trade_outcome("stonks", "SOFI", entry_price=10.0, exit_price=11.0, qty=5)
        assert result["outcome_label_warning"] is not None
        assert "connection to docker.klo refused" in result["outcome_label_warning"]
        assert fake_bankroll_module["written"] is True  # bankroll still updated despite PG failure

    def test_label_error_surfaces_as_warning_not_exception(self, fake_bankroll_module, monkeypatch):
        class FakeDecisions:
            @staticmethod
            def record_trade_close(**kwargs):
                return {"error": "no unlabeled training_examples row found for stonks/SOFI"}

        monkeypatch.setitem(sys.modules, "decisions", FakeDecisions)

        result = executor.close_trade_outcome("stonks", "SOFI", entry_price=10.0, exit_price=11.0, qty=5)
        assert result["outcome_label_warning"] == "no unlabeled training_examples row found for stonks/SOFI"
