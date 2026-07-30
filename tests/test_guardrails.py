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
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import executor  # noqa: E402
# Captured once, before any test's sys.modules["deployment_pressure"]
# monkeypatching -- a fresh `import deployment_pressure` inside a test that
# runs after TestGateConviction's autouse fixture would just re-bind to the
# cached fake module, not the real one.
import deployment_pressure as real_deployment_pressure  # noqa: E402


# A fixed Wednesday 12:00 ET during market hours, for gates that don't care
# about hours but would otherwise flake depending on when tests run.
MARKET_OPEN_TS = datetime.datetime(2026, 7, 22, 12, 0, tzinfo=ZoneInfo("America/New_York"))


DEFAULT_PARAMS = {
    "risk": {"max_position_pct": 6.0, "max_positions": 25, "conviction_floor": 0.5,
              "conviction_floor_min": 0.35,
              "duplicate_order_cooldown_seconds": 60, "stop_loss_pct": -10.0,
              "trailing_stop_pct": 5.0,
              "max_portfolio_risk_pct": 8.0,
              "drawdown_pause_pct": 15.0, "drawdown_halt_pct": 20.0},
    "risk_guards": {"max_positions_per_sector": 2, "order_count_audit_threshold_daily": 10},
    "guardrail_gates": {
        "cash": True, "position_size": True, "max_portfolio_risk": True, "max_positions": True,
        "sector_concentration": True, "hours": True, "conviction": True,
        "bankroll": True, "hard_stop": True, "trailing_stop": True,
        "position_size_trim": True, "duplicate_order": True, "order_idempotency": True,
        "order_count_audit": True,
        "drawdown_circuit_breaker": True,
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
# gate_max_portfolio_risk
# ─────────────────────────────────────────────────────────────────────────────


class TestGateMaxPortfolioRisk:
    def test_within_cap(self, params):
        # $5000 existing + $1000 proposed = $6000 exposure * 10% stop = $600
        # risk = 6% of $10000 equity, under the 8% cap
        context = {"portfolio_value": 10000, "positions": [{"symbol": "NVDA", "market_value": 5000.0}]}
        action = {"action": "BUY", "ticker": "SOFI", "quantity": 100, "price": 10.0}
        granted, reason = executor.gate_max_portfolio_risk(context, action)
        assert granted is True
        assert "6.0%" in reason

    def test_exceeds_cap(self, params):
        # $7000 existing + $2000 proposed = $9000 exposure * 10% stop = $900
        # risk = 9% of $10000 equity, over the 8% cap
        context = {"portfolio_value": 10000, "positions": [{"symbol": "NVDA", "market_value": 7000.0}]}
        action = {"action": "BUY", "ticker": "SOFI", "quantity": 200, "price": 10.0}
        granted, reason = executor.gate_max_portfolio_risk(context, action)
        assert granted is False
        assert "9.0%" in reason and "exceeds 8% cap" in reason

    def test_no_portfolio_value_fails_open(self, params):
        context = {"portfolio_value": 0, "positions": []}
        action = {"action": "BUY", "ticker": "SOFI", "quantity": 100, "price": 10.0}
        granted, reason = executor.gate_max_portfolio_risk(context, action)
        assert granted is True
        assert "fail-open" in reason

    def test_sell_skips(self, params):
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "SELL", "ticker": "SOFI", "quantity": 100, "price": 10.0}
        granted, _ = executor.gate_max_portfolio_risk(context, action)
        assert granted is True

    def test_tighter_stop_lowers_headroom(self, params):
        # Same exposure as test_within_cap but a 20% stop instead of 10% -
        # risk doubles to 12%, now over the 8% cap.
        params["risk"]["stop_loss_pct"] = -20.0
        context = {"portfolio_value": 10000, "positions": [{"symbol": "NVDA", "market_value": 5000.0}]}
        action = {"action": "BUY", "ticker": "SOFI", "quantity": 100, "price": 10.0}
        granted, reason = executor.gate_max_portfolio_risk(context, action)
        assert granted is False
        assert "12.0%" in reason


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

    def test_disabled_via_toggle_skips_in_check_order_chain(self, params, monkeypatch):
        """2026-07-24: no artificial position-count ceiling per Raf — gate
        disabled via the standard toggle mechanism, not a code change."""
        params["guardrail_gates"]["max_positions"] = False
        monkeypatch.setattr(executor, "GATES", {"max_positions": executor.gate_max_positions})
        monkeypatch.setattr(executor, "get_account", lambda account: {"cash": "100000", "equity": "100000"})
        monkeypatch.setattr(executor, "get_positions", lambda account: [{"symbol": s, "market_value": "100"} for s in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"])
        granted, reason, results = executor.check_order(
            "stonks", "BUY", "AA", 1, price=10.0, conviction=0.9, sector="Tech")
        assert granted is True
        assert results[0]["reason"] == "disabled via params.json guardrail_gates"


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
    @pytest.fixture(autouse=True)
    def isolated_deployment_pressure(self, monkeypatch):
        """2026-07-27: gate_conviction now imports deployment_pressure and
        reads its persisted state -- without isolation these tests would
        read/create the real state/deployment_pressure.json on disk, exactly
        the flakiness TestGateBankroll's module-mocking pattern exists to
        prevent. Fake conviction_floor mimics ticks=0 (no pressure) behavior:
        flat at base_floor, matching this class's existing 0.50-floor
        assertions unchanged."""
        fake_dp = type("M", (), {
            "read_state": staticmethod(lambda: {"consecutive_under_deployed_ticks": 0}),
            "conviction_floor": staticmethod(lambda base, floor_min, ticks: base),
        })
        monkeypatch.setitem(sys.modules, "deployment_pressure", fake_dp)

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

    def test_dynamic_floor_used_when_under_pressure(self, params, monkeypatch):
        """Confirms gate_conviction actually wires into deployment_pressure's
        real ramp math end-to-end, not just that isolation doesn't leak."""
        ticks = real_deployment_pressure.GRACE_TICKS + real_deployment_pressure.RAMP_TICKS
        fake_dp = type("M", (), {
            "read_state": staticmethod(lambda: {"consecutive_under_deployed_ticks": ticks}),
            "conviction_floor": staticmethod(real_deployment_pressure.conviction_floor),
        })
        monkeypatch.setitem(sys.modules, "deployment_pressure", fake_dp)
        # 0.4 conviction would fail the base 0.50 floor, but passes once
        # sustained pressure has fully dropped the floor to conviction_floor_min (0.35)
        granted, reason = executor.gate_conviction({}, {"action": "BUY", "conviction": 0.4})
        assert granted is True
        assert "0.35" in reason


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
        # record_order_submitted() also bumps experience.json's total_trades
        # (2026-07-27) — same leak risk as the daily-order-count file above
        # if left unpatched (real workspace-root experience.json, not under
        # STATE_DIR, so patching STATE_DIR alone doesn't cover it).
        monkeypatch.setattr(executor, "EXPERIENCE_PATH", tmp_path / "experience.json")

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


class TestGateOrderIdempotency:
    """2026-07-27: KRC bought 2x (same shape as IP on 2026-07-24) — both
    from overlapping sessions racing gate_duplicate_order's local,
    check-then-set state/recent_orders.json. This gate queries Alpaca's own
    live open-order book directly instead, closing the TOCTOU gap a
    local-file check can't: two concurrent processes (e.g. a cron tick and
    position_stream.py's websocket daemon) can each see "nothing recorded"
    before either writes, but they can't both see Alpaca report zero open
    orders if one of them already submitted one — Alpaca is the single
    shared source of truth. BUY-only (see gate_order_idempotency's
    docstring for why SELL doesn't need this)."""

    def _order(self, symbol, side="buy", order_id="ord-1"):
        return {"id": order_id, "symbol": symbol, "side": side}

    def test_buy_no_open_orders_passes(self, params, monkeypatch):
        monkeypatch.setattr(executor, "get_open_orders", lambda account, ticker=None: [])
        granted, reason = executor.gate_order_idempotency(
            {"account": "stonks"}, {"action": "BUY", "ticker": "KRC"})
        assert granted is True
        assert "no open BUY orders" in reason

    def test_buy_with_open_buy_order_blocks(self, params, monkeypatch):
        monkeypatch.setattr(
            executor, "get_open_orders",
            lambda account, ticker=None: [self._order("KRC", side="buy", order_id="ord-42")],
        )
        granted, reason = executor.gate_order_idempotency(
            {"account": "stonks"}, {"action": "BUY", "ticker": "KRC"})
        assert granted is False
        assert "already has 1 open BUY order" in reason
        assert "ord-42" in reason

    def test_buy_with_open_sell_order_does_not_block(self, params, monkeypatch):
        # An open SELL for the same ticker (e.g. a stop-loss GTC) is not a
        # duplicate BUY and must not block a fresh entry.
        monkeypatch.setattr(
            executor, "get_open_orders",
            lambda account, ticker=None: [self._order("KRC", side="sell")],
        )
        granted, reason = executor.gate_order_idempotency(
            {"account": "stonks"}, {"action": "BUY", "ticker": "KRC"})
        assert granted is True

    def test_buy_with_open_order_different_ticker_does_not_block(self, params, monkeypatch):
        # get_open_orders is called with a ticker filter in production, but
        # a gate shouldn't trust the caller/API to have filtered correctly.
        monkeypatch.setattr(
            executor, "get_open_orders",
            lambda account, ticker=None: [self._order("SOFI", side="buy")],
        )
        granted, reason = executor.gate_order_idempotency(
            {"account": "stonks"}, {"action": "BUY", "ticker": "KRC"})
        assert granted is True

    def test_sell_always_skipped(self, params, monkeypatch):
        def fail_if_called(account, ticker=None):
            raise AssertionError("gate_order_idempotency must not call Alpaca for a SELL")
        monkeypatch.setattr(executor, "get_open_orders", fail_if_called)
        granted, reason = executor.gate_order_idempotency(
            {"account": "stonks"}, {"action": "SELL", "ticker": "KRC"})
        assert granted is True
        assert "non-BUY" in reason

    def test_missing_ticker_fails_open(self, params, monkeypatch):
        monkeypatch.setattr(executor, "get_open_orders", lambda account, ticker=None: [])
        granted, reason = executor.gate_order_idempotency({"account": "stonks"}, {"action": "BUY"})
        assert granted is True
        assert "no ticker" in reason

    def test_missing_account_fails_open(self, params, monkeypatch):
        def fail_if_called(account, ticker=None):
            raise AssertionError("must not call Alpaca without an account")
        monkeypatch.setattr(executor, "get_open_orders", fail_if_called)
        granted, reason = executor.gate_order_idempotency({}, {"action": "BUY", "ticker": "KRC"})
        assert granted is True
        assert "fail-open" in reason

    def test_alpaca_api_error_fails_open(self, params, monkeypatch):
        def raise_error(account, ticker=None):
            raise RuntimeError("HTTP 429 rate limited")
        monkeypatch.setattr(executor, "get_open_orders", raise_error)
        granted, reason = executor.gate_order_idempotency(
            {"account": "stonks"}, {"action": "BUY", "ticker": "KRC"})
        assert granted is True
        assert "fail-open" in reason
        assert "429" in reason

    def test_multiple_open_orders_reports_count(self, params, monkeypatch):
        monkeypatch.setattr(
            executor, "get_open_orders",
            lambda account, ticker=None: [
                self._order("KRC", side="buy", order_id="a"),
                self._order("KRC", side="buy", order_id="b"),
            ],
        )
        granted, reason = executor.gate_order_idempotency(
            {"account": "stonks"}, {"action": "BUY", "ticker": "KRC"})
        assert granted is False
        assert "2 open BUY order" in reason


class TestOrderLock:
    """2026-07-30: gate_order_idempotency alone left a TOCTOU gap -- BFST
    double-bought 2026-07-29, two days after that gate shipped, the same
    pattern as IP (7/24) and KRC (7/27): two processes both query Alpaca's
    open-order book before either's order actually lands there. _order_lock
    makes check_order() -> place_order() atomic across processes. flock is
    per-open-file-description on Linux, so two separate open() calls in the
    same process still contend for the same lock -- that lets this test
    exercise real cross-process blocking semantics with a thread instead of
    spawning a subprocess."""

    def test_same_ticker_serializes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "ORDER_LOCK_DIR", tmp_path / "order_locks")
        timestamps = {}
        hold_seconds = 0.3

        def hold_first():
            with executor._order_lock("KRC"):
                timestamps["first_acquired"] = time.monotonic()
                time.sleep(hold_seconds)
            timestamps["first_released"] = time.monotonic()

        t = threading.Thread(target=hold_first)
        t.start()
        time.sleep(0.05)  # let the thread acquire the lock first

        with executor._order_lock("KRC"):
            timestamps["second_acquired"] = time.monotonic()
        t.join(timeout=5)

        # The second acquisition must have actually waited for the first
        # to release -- not just interleaved by luck.
        assert timestamps["second_acquired"] >= timestamps["first_released"]
        assert timestamps["second_acquired"] - timestamps["first_acquired"] >= hold_seconds * 0.9

    def test_different_tickers_do_not_contend(self, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "ORDER_LOCK_DIR", tmp_path / "order_locks")
        # Must not deadlock: different tickers use different lock files.
        with executor._order_lock("KRC"):
            with executor._order_lock("BFST"):
                pass


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
        # See TestGateDuplicateOrder.isolated_state — record_order_submitted()
        # also bumps experience.json's total_trades.
        monkeypatch.setattr(executor, "EXPERIENCE_PATH", tmp_path / "experience.json")

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


class TestGateDrawdownCircuitBreaker:
    """2026-07-23: competition-mode portfolio-level circuit breaker, adapted
    from paper-trading-rebuild/COMPETITION.md's >15%/>20% framework. BUY-only
    by design — SELLs (including stop-loss exits) always pass regardless of
    drawdown severity, see gate_drawdown_circuit_breaker's docstring."""

    @pytest.fixture(autouse=True)
    def isolated_state(self, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "PEAK_EQUITY_PATH", tmp_path / "peak_equity.json")
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)

    def test_no_portfolio_value_fails_open(self, params):
        granted, reason = executor.gate_drawdown_circuit_breaker({}, {"action": "BUY"})
        assert granted is True
        assert "fail-open" in reason

    def test_first_call_sets_peak_no_drawdown(self, params):
        granted, reason = executor.gate_drawdown_circuit_breaker(
            {"portfolio_value": 10000.0}, {"action": "BUY"})
        assert granted is True
        assert "0.0%" in reason

    def test_within_bounds_passes(self, params):
        executor.gate_drawdown_circuit_breaker({"portfolio_value": 10000.0}, {"action": "BUY"})
        granted, reason = executor.gate_drawdown_circuit_breaker(
            {"portfolio_value": 9200.0}, {"action": "BUY"})  # -8%
        assert granted is True

    def test_pause_threshold_blocks_buy(self, params):
        executor.gate_drawdown_circuit_breaker({"portfolio_value": 10000.0}, {"action": "BUY"})
        granted, reason = executor.gate_drawdown_circuit_breaker(
            {"portfolio_value": 8400.0}, {"action": "BUY"})  # -16%
        assert granted is False
        assert "PAUSED" in reason

    def test_halt_threshold_blocks_buy(self, params):
        executor.gate_drawdown_circuit_breaker({"portfolio_value": 10000.0}, {"action": "BUY"})
        granted, reason = executor.gate_drawdown_circuit_breaker(
            {"portfolio_value": 7900.0}, {"action": "BUY"})  # -21%
        assert granted is False
        assert "HALT" in reason

    def test_sell_always_passes_even_past_halt_threshold(self, params):
        executor.gate_drawdown_circuit_breaker({"portfolio_value": 10000.0}, {"action": "BUY"})
        granted, reason = executor.gate_drawdown_circuit_breaker(
            {"portfolio_value": 5000.0}, {"action": "SELL"})  # -50%
        assert granted is True
        assert "non-BUY" in reason

    def test_peak_ratchets_up_and_does_not_fall_back_down(self, params):
        executor.gate_drawdown_circuit_breaker({"portfolio_value": 10000.0}, {"action": "BUY"})
        executor.gate_drawdown_circuit_breaker({"portfolio_value": 12000.0}, {"action": "BUY"})
        # equity drops back to original starting point -- now a real ~16.7% drawdown from the new $12k peak
        granted, reason = executor.gate_drawdown_circuit_breaker(
            {"portfolio_value": 10000.0}, {"action": "BUY"})
        assert granted is False
        assert "$12,000.00" in reason

    def test_recovering_above_pause_threshold_unblocks(self, params):
        executor.gate_drawdown_circuit_breaker({"portfolio_value": 10000.0}, {"action": "BUY"})
        executor.gate_drawdown_circuit_breaker({"portfolio_value": 8400.0}, {"action": "BUY"})  # -16%, paused
        # equity recovers to within 15% of the $10k peak
        granted, _ = executor.gate_drawdown_circuit_breaker(
            {"portfolio_value": 8600.0}, {"action": "BUY"})  # -14%
        assert granted is True

    def test_disabled_via_toggle_skips_in_check_order_chain(self, params, monkeypatch):
        params["guardrail_gates"]["drawdown_circuit_breaker"] = False
        monkeypatch.setattr(executor, "GATES", {"drawdown_circuit_breaker": executor.gate_drawdown_circuit_breaker})
        monkeypatch.setattr(executor, "get_account", lambda account: {"cash": "100000", "equity": "5000"})
        monkeypatch.setattr(executor, "get_positions", lambda account: [])
        granted, reason, results = executor.check_order(
            "stonks", "BUY", "WSC", 1, price=10.0, conviction=0.9, sector="Tech")
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
        # gate_order_idempotency (2026-07-27) calls Alpaca directly — must be
        # patched too or these tests would attempt a real network call.
        monkeypatch.setattr(executor, "get_open_orders", lambda account, ticker=None: [])
        # Isolate from any real state/*.json so gate_duplicate_order and
        # gate_daily_order_count don't depend on filesystem state left over
        # from real trading (confirmed 2026-07-23: Stan had genuinely
        # placed 10 real orders today, tripping gate_daily_order_count for
        # real against these tests' un-isolated state).
        monkeypatch.setattr(executor, "RECENT_ORDERS_PATH", tmp_path / "recent_orders.json")
        monkeypatch.setattr(executor, "DAILY_ORDER_COUNT_PATH", tmp_path / "daily_order_count.json")
        monkeypatch.setattr(executor, "PEAK_EQUITY_PATH", tmp_path / "peak_equity.json")

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

    def test_order_idempotency_blocks_via_check_order_chain(self, params, mock_account, monkeypatch):
        """Integration check that check_order() actually wires the account
        into context so gate_order_idempotency can query Alpaca — a live
        open BUY order for the ticker must block the whole chain."""
        monkeypatch.setitem(executor.GATES, "hours", lambda c, a: (True, "market open"))
        monkeypatch.setattr(
            executor, "get_open_orders",
            lambda account, ticker=None: [{"id": "ord-9", "symbol": "SOFI", "side": "buy"}],
        )
        granted, reason, results = executor.check_order(
            "stonks", "BUY", "SOFI", 5, price=4.0, conviction=0.9,
        )
        assert granted is False
        assert "Blocked by order_idempotency" in reason


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
    updates bankroll.py's ceiling. 2026-07-27: same codepath now also
    mechanizes experience.json's total_wins/total_losses (see
    _load_experience's docstring for the parallel history)."""

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
        # experience.json lives at WORKSPACE_DIR root, not under STATE_DIR --
        # must be patched explicitly here or these tests would write into
        # the real production experience.json on every run.
        monkeypatch.setattr(executor, "EXPERIENCE_PATH", tmp_path / "experience.json")
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
        exp = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert exp["total_wins"] == 1
        assert exp["total_losses"] == 0
        assert exp["consecutive_wins"] == 1
        assert exp["consecutive_losses"] == 0

    def test_loss_marks_is_win_false(self, fake_bankroll_module, monkeypatch):
        class FakeDecisions:
            @staticmethod
            def record_trade_close(**kwargs):
                return {"labeled": True}

        monkeypatch.setitem(sys.modules, "decisions", FakeDecisions)

        executor.close_trade_outcome("stonks", "SOFI", entry_price=10.0, exit_price=9.0, qty=5)
        assert fake_bankroll_module["recalc"]["is_win"] is False
        exp = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert exp["total_losses"] == 1
        assert exp["total_wins"] == 0
        assert exp["consecutive_losses"] == 1
        assert exp["consecutive_wins"] == 0

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


# ─────────────────────────────────────────────────────────────────────────────
# experience.json bookkeeping — mechanized 2026-07-27, replacing the
# LLM-hand-edited counter that silently stopped tracking closed trades
# (Casper bug report: "experience.json shows 29 trades, unchanged since
# Friday" while 6 real trades executed that day). See _load_experience's
# docstring for why this is a real fix, not a "stat already superseded"
# no-op like experience.json's old current_level/milestones_unlocked
# fields (those genuinely were dead — replaced by bankroll.py's
# UNLOCK_TIERS, see that module's 2026-07-23 comment).
# ─────────────────────────────────────────────────────────────────────────────


class TestExperienceTracking:
    @pytest.fixture(autouse=True)
    def isolated_experience(self, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "EXPERIENCE_PATH", tmp_path / "experience.json")

    def test_load_missing_file_returns_zeroed_defaults(self):
        state = executor._load_experience()
        assert state["total_trades"] == 0
        assert state["total_wins"] == 0
        assert state["total_losses"] == 0
        assert state["consecutive_wins"] == 0
        assert state["consecutive_losses"] == 0
        assert "created_at" in state

    def test_load_corrupt_file_falls_back_to_defaults(self):
        executor.EXPERIENCE_PATH.write_text("{not valid json")
        state = executor._load_experience()
        assert state["total_trades"] == 0

    def test_load_preserves_existing_fields_not_touched_by_this_fix(self):
        # total_ticks stays LLM-hand-maintained (out of scope for this fix,
        # not part of Casper's bug report) -- must survive a trade-count
        # bump unchanged, not get reset to 0.
        executor.EXPERIENCE_PATH.write_text(json.dumps({
            "version": 1, "total_ticks": 200, "total_trades": 29,
            "total_wins": 12, "total_losses": 13,
            "consecutive_wins": 0, "consecutive_losses": 1,
            "created_at": "2026-07-20T16:30:00Z", "updated_at": "2026-07-27T19:30:00Z",
        }))
        executor.record_experience_trade()
        state = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert state["total_ticks"] == 200  # untouched
        assert state["total_trades"] == 30  # bumped

    def test_record_trade_increments_total_trades_only(self):
        executor.record_experience_trade()
        state = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert state["total_trades"] == 1
        assert state["total_wins"] == 0
        assert state["total_losses"] == 0

    def test_record_trade_updates_timestamp(self):
        executor.record_experience_trade()
        state = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert "updated_at" in state and state["updated_at"]

    def test_multiple_trades_accumulate(self):
        for _ in range(6):
            executor.record_experience_trade()
        state = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert state["total_trades"] == 6

    def test_win_increments_wins_and_win_streak(self):
        executor.record_experience_outcome(is_win=True)
        state = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert state["total_wins"] == 1
        assert state["consecutive_wins"] == 1
        assert state["consecutive_losses"] == 0

    def test_loss_increments_losses_and_loss_streak(self):
        executor.record_experience_outcome(is_win=False)
        state = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert state["total_losses"] == 1
        assert state["consecutive_losses"] == 1
        assert state["consecutive_wins"] == 0

    def test_streak_resets_on_opposite_outcome(self):
        executor.record_experience_outcome(is_win=True)
        executor.record_experience_outcome(is_win=True)
        state = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert state["consecutive_wins"] == 2

        executor.record_experience_outcome(is_win=False)
        state = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert state["consecutive_wins"] == 0  # win streak broken
        assert state["consecutive_losses"] == 1
        assert state["total_wins"] == 2  # lifetime totals unaffected by streak reset
        assert state["total_losses"] == 1

    def test_write_failure_does_not_raise(self, monkeypatch):
        """Fail-open, matching every other piece of state bookkeeping in
        this file -- a bookkeeping write failure must never look like a
        failed order (the order already executed by the time this runs)."""
        def broken_save(state):
            raise OSError("disk full")
        monkeypatch.setattr(executor, "_save_experience", broken_save)
        executor.record_experience_trade()  # must not raise
        executor.record_experience_outcome(is_win=True)  # must not raise

    def test_record_order_submitted_bumps_total_trades(self, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "RECENT_ORDERS_PATH", tmp_path / "recent_orders.json")
        monkeypatch.setattr(executor, "DAILY_ORDER_COUNT_PATH", tmp_path / "daily_order_count.json")
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        executor.record_order_submitted("KRC", "BUY", today="2026-07-27")
        state = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert state["total_trades"] == 1

    def test_record_order_submitted_counts_both_buy_and_sell(self, monkeypatch, tmp_path):
        # Matches the historical hand-maintained semantics confirmed in
        # git history: every executed order (BUY or SELL) bumped
        # total_trades, not just closes.
        monkeypatch.setattr(executor, "RECENT_ORDERS_PATH", tmp_path / "recent_orders.json")
        monkeypatch.setattr(executor, "DAILY_ORDER_COUNT_PATH", tmp_path / "daily_order_count.json")
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        executor.record_order_submitted("KRC", "BUY", today="2026-07-27")
        executor.record_order_submitted("KRC", "SELL", today="2026-07-27")
        state = json.loads(executor.EXPERIENCE_PATH.read_text())
        assert state["total_trades"] == 2
