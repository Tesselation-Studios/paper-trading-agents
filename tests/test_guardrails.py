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

# A fixed Wednesday 12:00 ET during market hours, for gates that don't care
# about hours but would otherwise flake depending on when tests run.
MARKET_OPEN_TS = datetime.datetime(2026, 7, 22, 12, 0, tzinfo=ZoneInfo("America/New_York"))


DEFAULT_PARAMS = {
    "risk": {"max_position_pct": 6.0, "max_positions": 25, "conviction_floor": 0.5,
              "conviction_floor_min": 0.35,
              "probe_position_pct": 1.5, "probe_max_dollars": 150.0,
              "duplicate_order_cooldown_seconds": 60, "stop_loss_pct": -10.0,
              "trailing_stop_pct": 5.0,
              "max_portfolio_risk_pct": 8.0,
              "drawdown_pause_pct": 15.0, "drawdown_halt_pct": 20.0,
              "long_play": {"enabled": True, "horizon_days": 3, "position_size_pct": 3.0,
                            "max_concurrent_long_plays": 2},
              "conviction_play": {"enabled": True, "position_size_pct": 10.0,
                                   "max_concurrent_conviction_plays": 5, "trail_multiplier": 1.5}},
    "risk_guards": {"max_positions_per_sector": 2, "order_count_audit_threshold_daily": 10},
    "guardrail_gates": {
        "cash": True, "position_size": True, "long_play": True, "conviction_play": True,
        "max_portfolio_risk": True, "max_positions": True,
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

    # ── play-type awareness (2026-08-01) ─────────────────────────────────
    # GATES is ordered and check_order() returns on the first rejection, so
    # this gate runs before gate_conviction_play/gate_long_play. Applying the
    # flat max_position_pct to every play type meant a conviction play was
    # rejected here before the gate that authorizes its larger size ever ran
    # -- live proof: positions held only {'standard': 41}, the conviction
    # bucket had never fired since shipping.

    def test_conviction_play_uses_conviction_cap(self, params):
        # $800 = 8% of portfolio: over the flat 6% cap, within conviction's 10%.
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "NVDA", "quantity": 8, "price": 100.0,
                   "play_type": "conviction"}
        granted, reason = executor.gate_position_size(context, action)
        assert granted is True
        assert "conviction-play cap" in reason

    def test_conviction_play_still_capped_at_its_own_limit(self, params):
        # $1200 = 12%, over conviction's own 10% cap -> still rejected.
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "NVDA", "quantity": 12, "price": 100.0,
                   "play_type": "conviction"}
        granted, reason = executor.gate_position_size(context, action)
        assert granted is False
        assert "conviction-play cap" in reason

    def test_long_play_uses_long_cap(self, params):
        # long_play.position_size_pct (3%) is SMALLER than max_position_pct
        # today, so this gate must tighten, not loosen, for a long play.
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "NVDA", "quantity": 4, "price": 100.0,
                   "play_type": "long"}
        granted, reason = executor.gate_position_size(context, action)
        assert granted is False
        assert "long-play cap" in reason

    def test_cap_follows_params_not_hardcoded_ordering(self, params):
        """Whichever way params.json is tuned, each bucket is measured
        against its own configured number -- the fix must not assume
        conviction > standard > long."""
        params["risk"]["conviction_play"]["position_size_pct"] = 2.0
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "NVDA", "quantity": 3, "price": 100.0,
                   "play_type": "conviction"}
        granted, _ = executor.gate_position_size(context, action)
        assert granted is False  # 3% now over the (lowered) 2% conviction cap


class TestGateLongPlay:
    """2026-07-30: risk.long_play -- small, short-horizon, evidence-gated
    exception to the normal trailing-stop schedule. This gate enforces the
    reduced size cap and the concurrent-count cap on BUYs tagged
    play_type='long'; everything else (standard BUYs, all SELLs) skips it."""

    class _FakeConn:
        def close(self):
            pass

    def _mock_open_positions(self, monkeypatch, rows):
        monkeypatch.setattr(executor.trader_db, "get_conn", lambda: self._FakeConn())
        monkeypatch.setattr(executor.trader_db, "get_open_positions", lambda conn: rows)

    def test_standard_buy_skips(self, params, monkeypatch):
        # Must not even touch trader_db for a non-long BUY.
        def fail_if_called():
            raise AssertionError("must not query trader_db for a standard BUY")
        monkeypatch.setattr(executor.trader_db, "get_conn", fail_if_called)
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "SOFI", "quantity": 10, "price": 4.0, "play_type": "standard"}
        granted, reason = executor.gate_long_play(context, action)
        assert granted is True
        assert "not a long play" in reason

    def test_sell_skips(self, params, monkeypatch):
        def fail_if_called():
            raise AssertionError("must not query trader_db for a SELL")
        monkeypatch.setattr(executor.trader_db, "get_conn", fail_if_called)
        action = {"action": "SELL", "ticker": "SOFI", "quantity": 10, "price": 4.0, "play_type": "long"}
        granted, _ = executor.gate_long_play({"portfolio_value": 10000, "positions": []}, action)
        assert granted is True

    def test_within_size_cap_and_no_concurrent_passes(self, params, monkeypatch):
        self._mock_open_positions(monkeypatch, [])
        # cap is 3.0% of 10000 = $300; this buy is $200 (2%)
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "BVS", "quantity": 20, "price": 10.0, "play_type": "long"}
        granted, reason = executor.gate_long_play(context, action)
        assert granted is True
        assert "0/2 concurrent" in reason

    def test_exceeds_long_play_size_cap(self, params, monkeypatch):
        self._mock_open_positions(monkeypatch, [])
        # cap is $300; this buy is $500 -- would fail the normal 6%/$600
        # max_position_pct too, but must be rejected on the TIGHTER
        # long-play cap specifically.
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "BVS", "quantity": 50, "price": 10.0, "play_type": "long"}
        granted, reason = executor.gate_long_play(context, action)
        assert granted is False
        assert "exceeds long-play cap of 3.0%" in reason

    def test_at_max_concurrent_blocks(self, params, monkeypatch):
        self._mock_open_positions(monkeypatch, [
            {"ticker": "AAA", "play_type": "long"},
            {"ticker": "BBB", "play_type": "long"},
        ])
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "CCC", "quantity": 20, "price": 10.0, "play_type": "long"}
        granted, reason = executor.gate_long_play(context, action)
        assert granted is False
        assert "max_concurrent_long_plays cap of 2" in reason

    def test_scaling_into_own_existing_long_play_not_counted_as_concurrent(self, params, monkeypatch):
        # Adding to a long play you already hold shouldn't count against
        # itself in the concurrency cap.
        self._mock_open_positions(monkeypatch, [
            {"ticker": "BVS", "play_type": "long"},
            {"ticker": "AAA", "play_type": "long"},
        ])
        context = {"portfolio_value": 10000, "positions": [{"symbol": "BVS", "market_value": 100.0}]}
        action = {"action": "BUY", "ticker": "BVS", "quantity": 10, "price": 10.0, "play_type": "long"}
        granted, reason = executor.gate_long_play(context, action)
        assert granted is True
        assert "1/2 concurrent" in reason

    def test_disabled_via_params_blocks(self, params, monkeypatch):
        params["risk"]["long_play"]["enabled"] = False
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "BVS", "quantity": 10, "price": 10.0, "play_type": "long"}
        granted, reason = executor.gate_long_play(context, action)
        assert granted is False
        assert "disabled" in reason

    def test_trader_db_error_fails_open(self, params, monkeypatch):
        def raise_error():
            raise RuntimeError("db locked")
        monkeypatch.setattr(executor.trader_db, "get_conn", raise_error)
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "BVS", "quantity": 10, "price": 10.0, "play_type": "long"}
        granted, reason = executor.gate_long_play(context, action)
        assert granted is True
        assert "fail-open" in reason

    def test_well_under_target_passes_with_undersized_note(self, params, monkeypatch):
        self._mock_open_positions(monkeypatch, [])
        # cap is 3.0% of 10000 = $300; this buy is $50 (0.5%), well under 1.5% (half the cap)
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "BVS", "quantity": 5, "price": 10.0, "play_type": "long"}
        granted, reason = executor.gate_long_play(context, action)
        assert granted is True
        assert "under 50% of long-play target range" in reason
        assert "position_sizing.py" in reason


class TestGateConvictionPlay:
    """params.json risk.conviction_play -- the researched, held-on-thesis
    bucket: larger size cap than long_play, its own concurrency cap. Same
    shape as TestGateLongPlay, sized up instead of down."""

    class _FakeConn:
        def close(self):
            pass

    def _mock_open_positions(self, monkeypatch, rows):
        monkeypatch.setattr(executor.trader_db, "get_conn", lambda: self._FakeConn())
        monkeypatch.setattr(executor.trader_db, "get_open_positions", lambda conn: rows)

    def test_standard_buy_skips(self, params, monkeypatch):
        def fail_if_called():
            raise AssertionError("must not query trader_db for a standard BUY")
        monkeypatch.setattr(executor.trader_db, "get_conn", fail_if_called)
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "SOFI", "quantity": 10, "price": 4.0, "play_type": "standard"}
        granted, reason = executor.gate_conviction_play(context, action)
        assert granted is True
        assert "not a conviction play" in reason

    def test_sell_skips(self, params, monkeypatch):
        def fail_if_called():
            raise AssertionError("must not query trader_db for a SELL")
        monkeypatch.setattr(executor.trader_db, "get_conn", fail_if_called)
        action = {"action": "SELL", "ticker": "SOFI", "quantity": 10, "price": 4.0, "play_type": "conviction"}
        granted, _ = executor.gate_conviction_play({"portfolio_value": 10000, "positions": []}, action)
        assert granted is True

    def test_within_size_cap_and_no_concurrent_passes(self, params, monkeypatch):
        self._mock_open_positions(monkeypatch, [])
        # cap is 10.0% of 10000 = $1000; this buy is $500 (5%)
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "AAPL", "quantity": 5, "price": 100.0, "play_type": "conviction"}
        granted, reason = executor.gate_conviction_play(context, action)
        assert granted is True
        assert "0/5 concurrent" in reason

    def test_exceeds_conviction_play_size_cap(self, params, monkeypatch):
        self._mock_open_positions(monkeypatch, [])
        # cap is $1000; this buy is $1500
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "AAPL", "quantity": 15, "price": 100.0, "play_type": "conviction"}
        granted, reason = executor.gate_conviction_play(context, action)
        assert granted is False
        assert "exceeds conviction-play cap of 10.0%" in reason

    def test_at_max_concurrent_blocks(self, params, monkeypatch):
        self._mock_open_positions(monkeypatch, [
            {"ticker": "AAA", "play_type": "conviction"},
            {"ticker": "BBB", "play_type": "conviction"},
            {"ticker": "CCC", "play_type": "conviction"},
            {"ticker": "DDD", "play_type": "conviction"},
            {"ticker": "EEE", "play_type": "conviction"},
        ])
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "FFF", "quantity": 5, "price": 100.0, "play_type": "conviction"}
        granted, reason = executor.gate_conviction_play(context, action)
        assert granted is False
        assert "max_concurrent_conviction_plays cap of 5" in reason

    def test_disabled_via_params_blocks(self, params, monkeypatch):
        params["risk"]["conviction_play"]["enabled"] = False
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "AAPL", "quantity": 5, "price": 100.0, "play_type": "conviction"}
        granted, reason = executor.gate_conviction_play(context, action)
        assert granted is False
        assert "disabled" in reason

    def test_trader_db_error_fails_open(self, params, monkeypatch):
        def raise_error():
            raise RuntimeError("db locked")
        monkeypatch.setattr(executor.trader_db, "get_conn", raise_error)
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "AAPL", "quantity": 5, "price": 100.0, "play_type": "conviction"}
        granted, reason = executor.gate_conviction_play(context, action)
        assert granted is True
        assert "fail-open" in reason

    def test_well_under_target_passes_with_undersized_note(self, params, monkeypatch):
        """2026-08-11: under half a conviction play's target range still
        passes (this isn't a new gate) but now surfaces a visible note
        instead of silently passing like every other gate check."""
        self._mock_open_positions(monkeypatch, [])
        # cap is 10.0% of 10000 = $1000; this buy is $200 (2%), well under 5% (half the cap)
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "IWM", "quantity": 2, "price": 100.0, "play_type": "conviction"}
        granted, reason = executor.gate_conviction_play(context, action)
        assert granted is True
        assert "under 50% of conviction-play target range" in reason
        assert "position_sizing.py" in reason

    def test_at_or_above_half_target_no_undersized_note(self, params, monkeypatch):
        self._mock_open_positions(monkeypatch, [])
        # cap is 10.0%; this buy is exactly 5.0% (the boundary, not under it)
        context = {"portfolio_value": 10000, "positions": []}
        action = {"action": "BUY", "ticker": "AAPL", "quantity": 5, "price": 100.0, "play_type": "conviction"}
        granted, reason = executor.gate_conviction_play(context, action)
        assert granted is True
        assert "under 50%" not in reason


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
# gate_catalyst_liquidity
# ─────────────────────────────────────────────────────────────────────────────


class TestGateCatalystLiquidity:
    def test_no_market_cap_fails_open(self, params):
        context = {"positions": []}
        action = {"action": "BUY", "ticker": "XYZ"}
        granted, reason = executor.gate_catalyst_liquidity(context, action)
        assert granted is True
        assert "fail-open" in reason

    def test_market_cap_above_threshold_not_applicable(self, params):
        context = {"positions": []}
        action = {"action": "BUY", "ticker": "AAPL", "market_cap": 600_000_000}
        granted, reason = executor.gate_catalyst_liquidity(context, action)
        assert granted is True
        assert "not applicable" in reason

    def test_sub_threshold_no_volume_data_fails_open(self, params):
        context = {"positions": []}
        action = {"action": "BUY", "ticker": "MBBC", "market_cap": 44_000_000}
        granted, reason = executor.gate_catalyst_liquidity(context, action)
        assert granted is True
        assert "fail-open" in reason

    def test_sub_threshold_thin_volume_blocks(self, params):
        context = {"positions": []}
        action = {"action": "BUY", "ticker": "MBBC", "market_cap": 44_000_000, "avg_dollar_volume": 5_000}
        granted, reason = executor.gate_catalyst_liquidity(context, action)
        assert granted is False
        assert "v1.18 skip" in reason

    def test_sub_threshold_adequate_volume_passes(self, params):
        context = {"positions": []}
        action = {"action": "BUY", "ticker": "MBBC", "market_cap": 44_000_000, "avg_dollar_volume": 60_000}
        granted, reason = executor.gate_catalyst_liquidity(context, action)
        assert granted is True
        assert "60,000" in reason


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
        assert "market open 09:30-16:00" in reason

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

    def test_holiday_blocked_even_on_a_weekday(self):
        # Christmas 2026 falls on a Friday -- a plain weekday/hours check
        # would pass this, the whole point of wiring in market_hours.py.
        christmas = datetime.datetime(2026, 12, 25, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        granted, reason = executor.gate_hours({"_test_now": christmas}, {})
        assert granted is False
        assert "holiday" in reason

    def test_floating_holiday_blocked(self):
        # Thanksgiving 2026 -- computed (4th Thursday of November), not a
        # fixed date, confirms the floating-holiday calculation is wired in.
        thanksgiving = datetime.datetime(2026, 11, 26, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        granted, reason = executor.gate_hours({"_test_now": thanksgiving}, {})
        assert granted is False
        assert "holiday" in reason

    def test_early_close_day_after_new_close_time_blocked(self):
        # Day after Thanksgiving 2026 (Nov 27, "Black Friday") is a 2pm early close.
        ts = datetime.datetime(2026, 11, 27, 14, 30, tzinfo=ZoneInfo("America/New_York"))
        granted, reason = executor.gate_hours({"_test_now": ts}, {})
        assert granted is False
        assert "early close" in reason

    def test_early_close_day_before_new_close_time_passes(self):
        ts = datetime.datetime(2026, 11, 27, 13, 0, tzinfo=ZoneInfo("America/New_York"))
        granted, _ = executor.gate_hours({"_test_now": ts}, {})
        assert granted is True

    def test_ordinary_weekday_not_blocked_as_holiday(self):
        # Sanity check the holiday wiring doesn't over-trigger on a normal day.
        granted, reason = executor.gate_hours({"_test_now": MARKET_OPEN_TS}, {})
        assert granted is True
        assert "holiday" not in reason


# ─────────────────────────────────────────────────────────────────────────────
# gate_conviction
# ─────────────────────────────────────────────────────────────────────────────


class TestGateConviction:
    """gate_conviction is a flat sanity floor now, not a deployment-pressure
    ratchet -- entry quality is decided by the gestalt Stan reasons over
    (tick_prompt.md step 8), not a numeric threshold formula."""

    def test_above_floor(self, params):
        granted, _ = executor.gate_conviction({}, {"action": "BUY", "conviction": 0.7})
        assert granted is True

    def test_below_floor(self, params):
        granted, reason = executor.gate_conviction({}, {"action": "BUY", "conviction": 0.3})
        assert granted is False
        assert "below sanity floor 0.50" in reason

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

    # ── play-type awareness (2026-08-01) ─────────────────────────────────
    # The ceiling is a dollar amount, so it doesn't share max_position_pct's
    # flat cap -- but it independently vetoed the whole conviction bucket
    # anyway (live: $679 ceiling vs ~$1,042 for a 10% conviction position).

    def test_conviction_play_floors_ceiling_at_its_configured_size(self, params, monkeypatch):
        fake_bankroll = type("M", (), {
            "read_bankroll": staticmethod(lambda: {"ceiling": 50.0}),
            "effective_ceiling": staticmethod(lambda state, equity: 679.0),
        })
        monkeypatch.setitem(sys.modules, "bankroll", fake_bankroll)
        # $900 > $679 ceiling, but within conviction_play's 10% of $10k.
        granted, reason = executor.gate_bankroll(
            {"portfolio_value": 10000.0},
            {"action": "BUY", "quantity": 9, "price": 100.0, "play_type": "conviction"})
        assert granted is True
        assert "floors the ceiling" in reason

    def test_conviction_play_still_bounded_by_its_own_size_cap(self, params, monkeypatch):
        fake_bankroll = type("M", (), {
            "read_bankroll": staticmethod(lambda: {"ceiling": 50.0}),
            "effective_ceiling": staticmethod(lambda state, equity: 679.0),
        })
        monkeypatch.setitem(sys.modules, "bankroll", fake_bankroll)
        # $1,500 exceeds both the ceiling and conviction_play's 10% ($1,000).
        granted, reason = executor.gate_bankroll(
            {"portfolio_value": 10000.0},
            {"action": "BUY", "quantity": 15, "price": 100.0, "play_type": "conviction"})
        assert granted is False
        assert "exceeds bankroll ceiling" in reason

    def test_standard_buy_ceiling_unchanged(self, params, monkeypatch):
        fake_bankroll = type("M", (), {
            "read_bankroll": staticmethod(lambda: {"ceiling": 50.0}),
            "effective_ceiling": staticmethod(lambda state, equity: 679.0),
        })
        monkeypatch.setitem(sys.modules, "bankroll", fake_bankroll)
        granted, reason = executor.gate_bankroll(
            {"portfolio_value": 10000.0},
            {"action": "BUY", "quantity": 9, "price": 100.0, "play_type": "standard"})
        assert granted is False
        assert "floors the ceiling" not in reason


class TestConvictionPlayReachesItsOwnGate:
    """Regression for the whole point of the 2026-08-01 gate fix: a
    conviction-sized BUY has to survive the full ordered gate chain, not
    just gate_conviction_play in isolation."""

    def test_conviction_sized_buy_passes_full_chain(self, params, monkeypatch):
        monkeypatch.setattr(executor, "get_account", lambda a: {"equity": "10000", "cash": "10000"})
        monkeypatch.setattr(executor, "get_positions", lambda a: [])
        monkeypatch.setattr(executor, "get_open_orders", lambda a, t=None: [])

        class _FakeConn:
            def close(self):
                pass
        monkeypatch.setattr(executor.trader_db, "get_conn", lambda: _FakeConn())
        monkeypatch.setattr(executor.trader_db, "get_open_positions", lambda conn: [])
        fake_bankroll = type("M", (), {
            "read_bankroll": staticmethod(lambda: {"ceiling": 679.0}),
            "effective_ceiling": staticmethod(lambda state, equity: 679.0),
        })
        monkeypatch.setitem(sys.modules, "bankroll", fake_bankroll)
        params["guardrail_gates"]["hours"] = False  # tested separately, don't depend on wall clock
        params["risk"]["max_portfolio_risk_pct"] = 50.0

        # 9% of a $10k portfolio: over the flat 6% cap AND over the $679
        # bankroll ceiling -- both of which used to reject it before
        # gate_conviction_play (which allows up to 10%) ever ran.
        granted, reason, results = executor.check_order(
            "stonks", "BUY", "NVDA", 9, price=100.0, conviction=0.8, play_type="conviction")
        assert granted is True, reason
        by_gate = {r["gate"]: r for r in results}
        assert by_gate["position_size"]["passed"] is True
        assert by_gate["bankroll"]["passed"] is True
        assert by_gate["conviction_play"]["passed"] is True


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

    def test_warn_mode_gate_that_would_reject_does_not_block(self, params, mock_account, monkeypatch):
        # conviction floor 0.5, conviction 0.1 would normally reject -- but
        # in "warn" mode the chain must still grant the order.
        params["guardrail_gates"]["conviction"] = "warn"
        monkeypatch.setitem(executor.GATES, "hours", lambda c, a: (True, "market open"))
        granted, reason, results = executor.check_order(
            "stonks", "BUY", "SOFI", 5, price=4.0, conviction=0.1,
        )
        assert granted is True
        conviction_result = next(r for r in results if r["gate"] == "conviction")
        assert conviction_result["passed"] is True
        assert conviction_result["warn_only"] is True
        assert conviction_result["would_have_blocked"] is True

    def test_warn_mode_gate_that_would_pass_shows_no_warn_flag(self, params, mock_account, monkeypatch):
        params["guardrail_gates"]["conviction"] = "warn"
        monkeypatch.setitem(executor.GATES, "hours", lambda c, a: (True, "market open"))
        granted, reason, results = executor.check_order(
            "stonks", "BUY", "SOFI", 5, price=4.0, conviction=0.9,
        )
        assert granted is True
        conviction_result = next(r for r in results if r["gate"] == "conviction")
        assert conviction_result["passed"] is True
        assert "warn_only" not in conviction_result

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


class TestRunGates:
    """2026-08-01: run_gates() was extracted out of check_order() so a
    simulated/historical caller (scripts/replay_order.py) can drive the
    same gate chain against a backtest-session context with zero Alpaca
    dependency. These tests call it directly -- no account/positions
    fixtures, no monkeypatching get_account/get_positions -- to lock in
    that it's genuinely a pure function of its arguments, unlike
    check_order() which is now a thin live-context wrapper around it."""

    def _context(self, **overrides):
        base = {"portfolio_value": 10000.0, "cash": 8000.0, "positions": []}
        base.update(overrides)
        return base

    def _action(self, **overrides):
        base = {"action": "BUY", "ticker": "SOFI", "quantity": 5, "price": 4.0,
                 "conviction": 0.9, "sector": None, "play_type": None}
        base.update(overrides)
        return base

    def test_no_alpaca_calls_needed(self, params, monkeypatch):
        """The whole point of the extraction: this must work with
        get_account/get_positions left completely unpatched -- if run_gates
        secretly still touched them, this would raise/hang on a real
        network call instead of passing cleanly."""
        monkeypatch.setitem(executor.GATES, "hours", lambda c, a: (True, "market open"))
        granted, reason, results = executor.run_gates(self._context(), self._action())
        assert granted is True
        assert len(results) == len(executor.GATES)

    def test_explicit_toggles_override_params_json(self, params, monkeypatch):
        """replay_order.py's whole mechanism for force-disabling
        live-concurrency-only gates (order_idempotency, etc.) for backtest
        sessions depends on this: an explicit toggles dict must win over
        whatever's in params.json, not just supplement it."""
        params["guardrail_gates"]["hours"] = "warn"  # live params.json says warn
        monkeypatch.setitem(executor.GATES, "hours", lambda c, a: (False, "market closed"))

        granted_default, _, results_default = executor.run_gates(self._context(), self._action())
        hours_default = next(r for r in results_default if r["gate"] == "hours")
        assert hours_default.get("warn_only") is True  # picked up params.json's "warn"

        granted_override, reason_override, results_override = executor.run_gates(
            self._context(), self._action(), toggles={"hours": False},
        )
        hours_override = next(r for r in results_override if r["gate"] == "hours")
        assert "disabled via params.json" in hours_override["reason"]
        assert granted_override is True

    def test_toggles_none_falls_back_to_params_json(self, params, monkeypatch):
        params["guardrail_gates"]["conviction"] = "warn"
        monkeypatch.setitem(executor.GATES, "hours", lambda c, a: (True, "market open"))
        granted, reason, results = executor.run_gates(
            self._context(), self._action(conviction=0.1), toggles=None,
        )
        conviction_result = next(r for r in results if r["gate"] == "conviction")
        assert conviction_result["warn_only"] is True

    def test_rejection_shape_matches_check_order(self, params, monkeypatch):
        """Same {"error"/rejection shape} whether called directly or via
        check_order() -- replay_order.py needs real gate feedback, not a
        friendlier simulated version."""
        monkeypatch.setitem(executor.GATES, "hours", lambda c, a: (True, "market open"))
        granted, reason, results = executor.run_gates(self._context(), self._action(conviction=0.1))
        assert granted is False
        assert "Blocked by conviction" in reason
        assert results[-1]["gate"] == "conviction"
        assert results[-1]["passed"] is False


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

    # ── Conviction plays get their own (larger) oversized cap ──────────────
    # 2026-08-10: this check used to compare every position against the flat
    # max_position_pct regardless of play_type, so a conviction play
    # correctly sized above 6% (per risk.conviction_play's own, larger cap)
    # got immediately force-trimmed as "oversized" -- confirmed live, the
    # SPY index-anchor was closed 3.5 minutes after entry citing the 6% cap
    # even though it was well under conviction_play's 10%.

    def _mock_conviction_play(self, monkeypatch, ticker):
        class _FakeConn:
            def close(self):
                pass
        monkeypatch.setattr(executor.trader_db, "get_conn", lambda: _FakeConn())
        monkeypatch.setattr(
            executor.trader_db, "get_open_positions",
            lambda conn: [{"ticker": ticker, "play_type": "conviction"}],
        )

    def test_conviction_play_within_its_own_cap_not_flagged_oversized(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -50.0, "trailing_stop_pct": 50.0, "max_position_pct": 6.0,
                           "conviction_play": {"position_size_pct": 10.0}}
        monkeypatch.setattr(executor, "get_account", lambda a: {"equity": "10000"})
        self._mock_conviction_play(monkeypatch, "NVDA")
        # $740 of $10,000 = 7.4% -- over the flat 6% cap, under conviction's 10%.
        monkeypatch.setattr(
            executor, "get_positions",
            lambda a: [self._position("NVDA", entry=185.0, current=185.0, qty=4, market_value=740.0)],
        )
        breaches = executor.check_stops("stonks")
        assert breaches == []

    def test_conviction_play_still_flagged_past_its_own_cap(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -50.0, "trailing_stop_pct": 50.0, "max_position_pct": 6.0,
                           "conviction_play": {"position_size_pct": 10.0}}
        monkeypatch.setattr(executor, "get_account", lambda a: {"equity": "10000"})
        self._mock_conviction_play(monkeypatch, "NVDA")
        # $1200 of $10,000 = 12% -- over conviction's own 10% cap too.
        monkeypatch.setattr(
            executor, "get_positions",
            lambda a: [self._position("NVDA", entry=185.0, current=185.0, qty=6, market_value=1200.0)],
        )
        breaches = executor.check_stops("stonks")
        oversized = [b for b in breaches if b["stop_type"] == "oversized"]
        assert len(oversized) == 1
        assert "10%" in oversized[0]["reason"]

    def test_index_anchor_ticker_exempt_from_oversized_entirely(self, params, monkeypatch, tmp_path):
        """2026-08-10 (Raf's direction): index-anchor tickers (SPY/QQQ/DIA/
        IWM) are exempt from the oversized trim entirely on the upside, not
        just capped at conviction_play's own (now 20%) cap -- current policy
        is 'don't buy more, sell it down for cash' rather than force-trim.
        A non-anchor conviction ticker at the same % is still flagged (see
        test_conviction_play_still_flagged_past_its_own_cap above), so this
        isolates the exemption to the whitelist, not conviction plays broadly."""
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"] = {"stop_loss_pct": -50.0, "trailing_stop_pct": 50.0, "max_position_pct": 6.0,
                           "conviction_play": {"position_size_pct": 20.0}}
        monkeypatch.setattr(executor, "get_account", lambda a: {"equity": "10000"})
        self._mock_conviction_play(monkeypatch, "SPY")
        # $5000 of $10,000 = 50% -- way past even the 20% conviction cap.
        monkeypatch.setattr(
            executor, "get_positions",
            lambda a: [self._position("SPY", entry=773.26, current=773.26, qty=6, market_value=5000.0)],
        )
        breaches = executor.check_stops("stonks")
        oversized = [b for b in breaches if b["stop_type"] == "oversized"]
        assert oversized == []

    # ── Long plays (2026-07-30, risk.long_play) ───────────────────────────
    # A long play is exempt from the trailing-stop schedule only while its
    # predicted_by_date hasn't arrived; the hard stop always applies
    # regardless. At the deadline, check_stops() resolves it mechanically
    # (flips play_type back to 'standard', labels training_examples) --
    # this must fire once, not force a sell.

    def _mock_long_play(self, monkeypatch, ticker, predicted_by_date, resolve_calls=None, label_calls=None):
        class _FakeConn:
            def close(self):
                pass
        monkeypatch.setattr(executor.trader_db, "get_conn", lambda: _FakeConn())
        monkeypatch.setattr(
            executor.trader_db, "get_open_positions",
            lambda conn: [{"ticker": ticker, "play_type": "long", "predicted_by_date": predicted_by_date,
                            "prediction_reason": "earnings beat expected"}],
        )
        if resolve_calls is not None:
            def fake_resolve(conn, ticker, updated_at):
                resolve_calls.append(ticker)
            monkeypatch.setattr(executor.trader_db, "resolve_long_play", fake_resolve)
        if label_calls is not None:
            # 2026-08-01: entry rows only -- see trader_db.find_entry_training_example.
            monkeypatch.setattr(
                executor.trader_db, "find_entry_training_example",
                lambda conn, ticker, position_entry_time=None: {"id": 42, "example_type": "entry"},
            )

            def fake_label(conn, training_example_id, trade_id, label_win, label_return_pct, label_horizon):
                label_calls.append((training_example_id, label_win, label_return_pct, label_horizon))
            monkeypatch.setattr(executor.trader_db, "label_training_example", fake_label)

    def _relative_date(self, days):
        return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).strftime("%Y-%m-%d")

    def test_active_long_play_skips_trailing_stop(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"]["stop_loss_pct"] = -50.0  # wide, won't trigger
        params["risk"]["trailing_stop_pct"] = 5.0
        params["guardrail_gates"]["position_size_trim"] = False
        future = self._relative_date(7)
        self._mock_long_play(monkeypatch, "BVS", future)
        # peak 15 -> drop to 14.2 would normally breach the 5% trailing stop
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("BVS", 10.0, 15.0)])
        executor.check_stops("stonks")
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("BVS", 10.0, 14.2)])
        breaches = executor.check_stops("stonks")
        assert breaches == []

    def test_active_long_play_hard_stop_still_applies(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"]["stop_loss_pct"] = -10.0
        params["risk"]["trailing_stop_pct"] = 5.0
        params["guardrail_gates"]["position_size_trim"] = False
        future = self._relative_date(7)
        self._mock_long_play(monkeypatch, "BVS", future)
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("BVS", 10.0, 8.5)])  # -15%
        breaches = executor.check_stops("stonks")
        assert len(breaches) == 1
        assert breaches[0]["stop_type"] == "hard"

    def test_long_play_resolves_at_deadline(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"]["stop_loss_pct"] = -50.0
        params["risk"]["trailing_stop_pct"] = 50.0
        params["guardrail_gates"]["position_size_trim"] = False
        past = self._relative_date(-1)
        resolve_calls, label_calls = [], []
        self._mock_long_play(monkeypatch, "BVS", past, resolve_calls=resolve_calls, label_calls=label_calls)
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("BVS", 10.0, 11.0)])  # +10%, "hit"
        breaches = executor.check_stops("stonks")
        assert len(breaches) == 1
        assert breaches[0]["stop_type"] == "long_play_resolved"
        assert breaches[0]["long_play_hit"] is True
        assert breaches[0]["loss_pct"] == pytest.approx(10.0)
        assert resolve_calls == ["BVS"]
        assert len(label_calls) == 1
        te_id, label_win, label_return_pct, label_horizon = label_calls[0]
        assert te_id == 42
        assert label_win == 1
        assert label_return_pct == pytest.approx(10.0)
        assert label_horizon == "long_play_prediction"

    def test_long_play_resolution_records_miss_when_down(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"]["stop_loss_pct"] = -50.0
        params["risk"]["trailing_stop_pct"] = 50.0
        params["guardrail_gates"]["position_size_trim"] = False
        past = self._relative_date(-1)
        resolve_calls, label_calls = [], []
        self._mock_long_play(monkeypatch, "BVS", past, resolve_calls=resolve_calls, label_calls=label_calls)
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("BVS", 10.0, 9.0)])  # -10%, "miss"
        breaches = executor.check_stops("stonks")
        assert breaches[0]["long_play_hit"] is False
        assert label_calls[0][1] == 0  # label_win

    def test_long_play_toggle_disabled_skips_trader_db(self, params, monkeypatch, tmp_path):
        monkeypatch.setattr(executor, "STATE_DIR", tmp_path)
        monkeypatch.setattr(executor, "STOPS_STATE_PATH", tmp_path / "guardrail_stops.json")
        params["risk"]["stop_loss_pct"] = -50.0
        params["risk"]["trailing_stop_pct"] = 50.0
        params["guardrail_gates"]["position_size_trim"] = False
        params["guardrail_gates"]["long_play"] = False

        def fail_if_called():
            raise AssertionError("must not query trader_db when guardrail_gates.long_play is False")
        monkeypatch.setattr(executor.trader_db, "get_conn", fail_if_called)
        monkeypatch.setattr(executor, "get_positions", lambda a: [self._position("BVS", 10.0, 10.5)])
        breaches = executor.check_stops("stonks")
        assert breaches == []


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
            def record_trade_close(trader_id, ticker, trade_id, pnl, return_pct, position_entry_time=None):
                recorded.update(trader_id=trader_id, ticker=ticker, pnl=pnl, return_pct=return_pct,
                                 position_entry_time=position_entry_time)
                return {"training_example_id": 1, "labeled": True}

        monkeypatch.setitem(sys.modules, "decisions", FakeDecisions)

        result = executor.close_trade_outcome("stonks", "SOFI", entry_price=10.0, exit_price=11.0, qty=5,
                                               position_entry_time="2026-08-01T13:00:00+00:00")

        assert result["pnl"] == pytest.approx(5.0)
        assert result["return_pct"] == pytest.approx(10.0)
        assert result["outcome_label_warning"] is None
        assert fake_bankroll_module["recalc"] == {"pnl": pytest.approx(5.0), "is_win": True}
        assert fake_bankroll_module["written"] is True
        assert recorded == {"trader_id": "stonks", "ticker": "SOFI", "pnl": pytest.approx(5.0),
                             "return_pct": pytest.approx(10.0),
                             "position_entry_time": "2026-08-01T13:00:00+00:00"}
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


# ─────────────────────────────────────────────────────────────────────────────
# Broker-side protective stops (2026-08-01)
#
# check_stops() only ever DETECTED breaches and handed them to the agent to
# act on in the same tick — nothing existed at the broker, so any tick that
# timed out (~23% of them) or any gateway restart left positions with no
# enforced floor at all. These cover the resting stop order and the
# cancel/replace cleanup that keeps it from conflicting with a real exit.
# ─────────────────────────────────────────────────────────────────────────────


class TestProtectiveStopPricing:
    def test_stop_price_matches_hard_stop_pct(self, params):
        params["risk"]["stop_loss_pct"] = -10.0
        assert executor.protective_stop_price(10.0) == pytest.approx(9.0)

    def test_stop_price_follows_params_whatever_the_value(self, params):
        params["risk"]["stop_loss_pct"] = -7.5
        assert executor.protective_stop_price(20.0) == pytest.approx(18.5)

    def test_rounds_down_to_penny_at_or_above_one_dollar(self):
        # Rounding must never tighten a stop into a price it wasn't meant
        # to trigger at.
        assert executor._round_stop_price(9.019) == pytest.approx(9.01)

    def test_sub_dollar_keeps_four_decimals(self):
        # Alpaca only accepts sub-penny increments below $1.00.
        assert executor._round_stop_price(0.45678) == pytest.approx(0.4567)

    def test_identifies_protective_stop_orders(self):
        assert executor.is_protective_stop_order({"side": "sell", "type": "stop"}) is True
        assert executor.is_protective_stop_order({"side": "sell", "order_type": "trailing_stop"}) is True
        assert executor.is_protective_stop_order({"side": "buy", "type": "stop"}) is False
        assert executor.is_protective_stop_order({"side": "sell", "type": "market"}) is False
        assert executor.is_protective_stop_order("not a dict") is False


class TestEnsureProtectiveStop:
    def _position(self, symbol="SOFI", qty="10", entry="10.00", current="10.50"):
        return {"symbol": symbol, "qty": qty, "avg_entry_price": entry,
                 "current_price": current, "market_value": str(float(qty) * float(current))}

    def test_places_stop_at_hard_stop_price(self, params, monkeypatch):
        params["risk"]["stop_loss_pct"] = -10.0
        placed = []
        monkeypatch.setattr(executor, "get_open_orders", lambda a, t=None: [])
        monkeypatch.setattr(executor, "place_stop_order",
                             lambda a, t, q, p: placed.append((t, q, p)) or {"id": "stop-1"})
        result = executor.ensure_protective_stop("stonks", "SOFI", position=self._position())
        assert result["status"] == "placed"
        assert placed == [("SOFI", 10, pytest.approx(9.0))]

    def test_leaves_correct_existing_stop_alone(self, params, monkeypatch):
        """Called every tick — an already-correct stop must not be
        cancelled and re-placed, that's pure order churn."""
        params["risk"]["stop_loss_pct"] = -10.0
        monkeypatch.setattr(executor, "get_open_orders", lambda a, t=None: [
            {"id": "stop-1", "symbol": "SOFI", "side": "sell", "type": "stop",
             "stop_price": "9.00", "qty": "10"},
        ])
        monkeypatch.setattr(executor, "place_stop_order", lambda *a, **k: pytest.fail("must not re-place"))
        monkeypatch.setattr(executor, "cancel_order", lambda *a, **k: pytest.fail("must not cancel"))
        result = executor.ensure_protective_stop("stonks", "SOFI", position=self._position())
        assert result["status"] == "already_set"

    def test_replaces_stop_with_wrong_size(self, params, monkeypatch):
        """A trim leaves a stop covering more shares than are held."""
        params["risk"]["stop_loss_pct"] = -10.0
        cancelled, placed = [], []
        monkeypatch.setattr(executor, "get_open_orders", lambda a, t=None: [
            {"id": "stop-old", "symbol": "SOFI", "side": "sell", "type": "stop",
             "stop_price": "9.00", "qty": "25"},
        ] if not cancelled else [])
        monkeypatch.setattr(executor, "cancel_order", lambda a, oid: cancelled.append(oid) or True)
        monkeypatch.setattr(executor, "place_stop_order",
                             lambda a, t, q, p: placed.append((t, q, p)) or {"id": "stop-new"})
        result = executor.ensure_protective_stop("stonks", "SOFI", position=self._position())
        assert result["status"] == "placed"
        assert cancelled == ["stop-old"]
        assert placed == [("SOFI", 10, pytest.approx(9.0))]

    def test_no_position_cancels_orphan_stop(self, params, monkeypatch):
        cancelled = []
        monkeypatch.setattr(executor, "get_positions", lambda a: [])
        monkeypatch.setattr(executor, "get_open_orders", lambda a, t=None: [
            {"id": "stop-orphan", "symbol": "SOFI", "side": "sell", "type": "stop",
             "stop_price": "9.00", "qty": "10"},
        ])
        monkeypatch.setattr(executor, "cancel_order", lambda a, oid: cancelled.append(oid) or True)
        result = executor.ensure_protective_stop("stonks", "SOFI")
        assert result["status"] == "no_position"
        assert cancelled == ["stop-orphan"]

    def test_already_through_the_floor_does_not_submit(self, params, monkeypatch):
        """Alpaca rejects a sell stop at/above the current price, and the
        position needs a market exit now — check_stops() reports it."""
        params["risk"]["stop_loss_pct"] = -10.0
        monkeypatch.setattr(executor, "get_open_orders", lambda a, t=None: [])
        monkeypatch.setattr(executor, "place_stop_order", lambda *a, **k: pytest.fail("must not submit"))
        result = executor.ensure_protective_stop(
            "stonks", "SOFI", position=self._position(current="8.50"))
        assert result["status"] == "below_stop_already"

    def test_disabled_via_params_toggle(self, params, monkeypatch):
        params["guardrail_gates"]["broker_stop_order"] = False
        monkeypatch.setattr(executor, "place_stop_order", lambda *a, **k: pytest.fail("must not submit"))
        result = executor.ensure_protective_stop("stonks", "SOFI", position=self._position())
        assert result["status"] == "disabled"

    def test_api_error_never_raises(self, params, monkeypatch):
        params["risk"]["stop_loss_pct"] = -10.0
        monkeypatch.setattr(executor, "get_open_orders", lambda a, t=None: [])

        def boom(*a, **k):
            raise RuntimeError("alpaca 500")
        monkeypatch.setattr(executor, "place_stop_order", boom)
        result = executor.ensure_protective_stop("stonks", "SOFI", position=self._position())
        assert result["status"] == "error"


class TestReconcileProtectiveStops:
    def test_places_missing_stops_and_cancels_orphans(self, params, monkeypatch):
        params["risk"]["stop_loss_pct"] = -10.0
        placed, cancelled = [], []
        orders = [
            {"id": "stop-dead", "symbol": "GONE", "side": "sell", "type": "stop",
             "stop_price": "5.00", "qty": "3"},
        ]
        monkeypatch.setattr(executor, "get_positions", lambda a: [
            {"symbol": "SOFI", "qty": "10", "avg_entry_price": "10.00",
             "current_price": "10.50", "market_value": "105.00"},
        ])

        def fake_open_orders(account, ticker=None):
            if ticker is None:
                return list(orders)
            return [o for o in orders if o["symbol"] == ticker]
        monkeypatch.setattr(executor, "get_open_orders", fake_open_orders)
        monkeypatch.setattr(executor, "cancel_order", lambda a, oid: cancelled.append(oid) or True)
        monkeypatch.setattr(executor, "place_stop_order",
                             lambda a, t, q, p: placed.append((t, q, p)) or {"id": "stop-new"})

        results = executor.reconcile_protective_stops("stonks")
        statuses = {r.get("ticker"): r["status"] for r in results}
        assert statuses["SOFI"] == "placed"
        assert statuses["GONE"] == "orphan_cancelled"
        assert cancelled == ["stop-dead"]
        assert placed == [("SOFI", 10, pytest.approx(9.0))]

    def test_positions_read_failure_never_raises(self, params, monkeypatch):
        def boom(account):
            raise RuntimeError("alpaca down")
        monkeypatch.setattr(executor, "get_positions", boom)
        results = executor.reconcile_protective_stops("stonks")
        assert results[0]["status"] == "error"


class TestCancelProtectiveStops:
    def test_cancels_only_sell_stops(self, params, monkeypatch):
        cancelled = []
        monkeypatch.setattr(executor, "get_open_orders", lambda a, t=None: [
            {"id": "stop-1", "symbol": "SOFI", "side": "sell", "type": "stop"},
            {"id": "limit-1", "symbol": "SOFI", "side": "sell", "type": "limit"},
            {"id": "buy-1", "symbol": "SOFI", "side": "buy", "type": "stop"},
        ])
        monkeypatch.setattr(executor, "cancel_order", lambda a, oid: cancelled.append(oid) or True)
        monkeypatch.setattr(executor, "_wait_orders_cleared", lambda *a, **k: True)
        assert executor.cancel_protective_stops("stonks", "SOFI") == ["stop-1"]
        assert cancelled == ["stop-1"]

    def test_cancel_failure_is_swallowed(self, params, monkeypatch):
        """A 422 means the order is already gone; the SELL that follows is
        where a genuine problem would surface loudly."""
        monkeypatch.setattr(executor, "get_open_orders", lambda a, t=None: [
            {"id": "stop-1", "symbol": "SOFI", "side": "sell", "type": "stop"},
        ])

        def boom(a, oid):
            raise RuntimeError("422 order not cancelable")
        monkeypatch.setattr(executor, "cancel_order", boom)
        assert executor.cancel_protective_stops("stonks", "SOFI") == []


class TestReconcileStoppedOutPositions:
    """A broker-side stop can close a position while no tick is running --
    the case the resting order exists for. Without this reconciliation the
    new stop order would create exactly the state drift the labeling fix is
    about: gone at the broker, still 'open' locally, training row unlabeled,
    bankroll never told about the loss."""

    class _FakeConn:
        def close(self):
            pass

    def _mock_db(self, monkeypatch, open_rows, closed):
        monkeypatch.setattr(executor.trader_db, "get_conn", lambda: self._FakeConn())
        monkeypatch.setattr(executor.trader_db, "get_open_positions", lambda conn: open_rows)

        def fake_close(conn, ticker, closed_at, close_reason, realized_pnl, realized_return_pct):
            closed.append((ticker, close_reason, realized_pnl))
        monkeypatch.setattr(executor.trader_db, "close_position", fake_close)

    def test_closes_books_from_the_broker_fill(self, params, monkeypatch, tmp_path):
        closed, outcomes = [], []
        monkeypatch.setattr(executor, "get_positions", lambda a: [])
        self._mock_db(monkeypatch, [
            {"ticker": "SOFI", "shares": 10.0, "entry_price": 10.0, "entry_time": "t1"},
        ], closed)
        monkeypatch.setattr(executor, "get_closed_orders", lambda a, t, limit=10: [
            {"id": "stop-1", "side": "sell", "status": "filled",
             "filled_avg_price": "9.00", "filled_qty": "10"},
        ])
        monkeypatch.setattr(executor, "close_trade_outcome",
                             lambda *a, **k: outcomes.append((a, k)) or
                             {"pnl": -10.0, "return_pct": -10.0, "outcome_label_warning": None})
        monkeypatch.setattr(executor, "record_order_submitted", lambda *a, **k: None)

        results = executor.reconcile_stopped_out_positions("stonks")
        assert results[0]["status"] == "closed_from_broker_fill"
        assert results[0]["exit_price"] == pytest.approx(9.0)
        assert closed[0][0] == "SOFI"
        assert "broker-side stop fill" in closed[0][1]
        # the entry_time correlation key must reach the labeling call
        assert outcomes[0][1]["position_entry_time"] == "t1"

    def test_still_held_positions_untouched(self, params, monkeypatch):
        closed = []
        monkeypatch.setattr(executor, "get_positions", lambda a: [{"symbol": "SOFI"}])
        self._mock_db(monkeypatch, [
            {"ticker": "SOFI", "shares": 10.0, "entry_price": 10.0, "entry_time": "t1"},
        ], closed)
        monkeypatch.setattr(executor, "get_closed_orders",
                             lambda *a, **k: pytest.fail("must not look up a held position"))
        assert executor.reconcile_stopped_out_positions("stonks") == []
        assert closed == []

    def test_no_fill_found_reports_unresolved_rather_than_guessing(self, params, monkeypatch):
        """Never invent a P&L off a stale entry price -- bankroll and the
        win/loss label are downstream of this number."""
        closed = []
        monkeypatch.setattr(executor, "get_positions", lambda a: [])
        self._mock_db(monkeypatch, [
            {"ticker": "SOFI", "shares": 10.0, "entry_price": 10.0, "entry_time": "t1"},
        ], closed)
        monkeypatch.setattr(executor, "get_closed_orders", lambda a, t, limit=10: [])
        monkeypatch.setattr(executor, "close_trade_outcome",
                             lambda *a, **k: pytest.fail("must not price an unknown exit"))
        results = executor.reconcile_stopped_out_positions("stonks")
        assert results[0]["status"] == "unresolved"
        assert closed == []

    def test_api_failure_never_raises(self, params, monkeypatch):
        def boom(account):
            raise RuntimeError("alpaca down")
        monkeypatch.setattr(executor, "get_positions", boom)
        assert executor.reconcile_stopped_out_positions("stonks")[0]["status"] == "error"
