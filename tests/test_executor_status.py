#!/usr/bin/env python3
"""Tests for executor.py's `--action status` output, specifically the
`gate_status` block (2026-08-02) -- a fresh-computed, read-only snapshot of
every "count vs cap" guardrail, folded into the always-called status action
so the tick agent can't self-exclude a candidate based on a stale remembered
number (see executor.py's inline comment on the FLXS incident for the
motivating root cause).

No existing test in this repo exercised the `status` action at all before
this file -- genuinely new coverage, not an extension of an existing suite.
Same conventions as test_executor_audit.py: real sqlite3 under tmp_path, no
DB mocking, only Alpaca HTTP faked via urllib.request.urlopen.
"""
import json
import sys
import urllib.request
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import alpaca_client  # noqa: E402
import executor  # noqa: E402
import trader_db  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deployment_pressure  # noqa: E402


DEFAULT_PARAMS = {
    "risk": {"max_positions": 25},
    "risk_guards": {"max_positions_per_sector": 5, "order_count_audit_threshold_daily": 30},
    "guardrail_gates": {"sector_concentration": "warn", "order_count_audit": False, "max_positions": False},
    "watchlist": {"discovery_urgency": {"cash_threshold_pct": 70.0}},
    "tick": {},
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
    monkeypatch.setenv("ALPACA_STONKS_KEY", "test-key")
    monkeypatch.setenv("ALPACA_STONKS_SECRET", "test-secret")
    state_dir = tmp_path / "state"
    monkeypatch.setattr(executor, "STATE_DIR", state_dir)
    monkeypatch.setattr(alpaca_client, "STATE_DIR", state_dir)
    monkeypatch.setattr(executor, "DAILY_ORDER_COUNT_PATH", state_dir / "daily_order_count.json")
    monkeypatch.setattr(alpaca_client, "DAILY_ORDER_COUNT_PATH", state_dir / "daily_order_count.json")
    monkeypatch.setattr(executor, "PEAK_EQUITY_PATH", state_dir / "peak_equity.json")
    monkeypatch.setattr(alpaca_client, "PEAK_EQUITY_PATH", state_dir / "peak_equity.json")
    monkeypatch.setattr(deployment_pressure, "STATE_FILE", state_dir / "deployment_pressure.json")
    # position_sizing.py's scorecard paths are module-level constants
    # (computed once at import from a fixed on-disk location, not from
    # executor.STATE_DIR) -- without this, status's tier_capacity block
    # would read the real state/signal_scorecard.json / tree_scorecard.json
    # in every test that doesn't explicitly monkeypatch graduation_readiness.
    import position_sizing
    monkeypatch.setattr(position_sizing, "SIGNAL_SCORECARD_PATH", state_dir / "signal_scorecard.json")
    monkeypatch.setattr(position_sizing, "TREE_SCORECARD_PATH", state_dir / "tree_scorecard.json")
    yield


@pytest.fixture
def params(monkeypatch):
    data = json.loads(json.dumps(DEFAULT_PARAMS))  # deep copy, per-test mutation safe
    monkeypatch.setattr(executor, "load_params", lambda: data)
    monkeypatch.setattr(alpaca_client, "load_params", lambda: data)
    return data


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen_for_status(positions_response=None):
    positions_response = positions_response or []

    def fake_urlopen(req):
        url = req.full_url
        if url.endswith("/v2/positions"):
            return _FakeResponse(positions_response)
        if url.endswith("/v2/account"):
            return _FakeResponse({"equity": "10000", "cash": "5000", "buying_power": "10000", "daytrade_count": 0})
        return _FakeResponse({})
    return fake_urlopen


def _run_status(monkeypatch, capsys, positions_response=None):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_for_status(positions_response))
    monkeypatch.setattr(sys, "argv", ["executor.py", "--account", "stonks", "--action", "status"])
    executor.main()
    return json.loads(capsys.readouterr().out)


def _seed_open_position(ticker, sector, play_type=None):
    conn = trader_db.get_conn()
    try:
        kwargs = {"play_type": play_type} if play_type else {}
        trader_db.upsert_position(conn, ticker=ticker, shares=1.0, entry_price=10.0,
                                   entry_time="2026-08-02T10:00:00Z", sector=sector, **kwargs)
    finally:
        conn.close()


class TestGateStatusSectorConcentration:
    def test_reports_sector_counts_against_cap(self, monkeypatch, capsys, params):
        _seed_open_position("AAA", "Consumer Cyclical")
        _seed_open_position("BBB", "Consumer Cyclical")

        result = _run_status(monkeypatch, capsys)

        by_sector = result["gate_status"]["sector_concentration"]["by_sector"]
        assert by_sector["Consumer Cyclical"] == {"open": 2, "cap": 5, "at_cap": False}
        assert result["gate_status"]["sector_concentration"]["cap_per_sector"] == 5
        assert result["gate_status"]["sector_concentration"]["gate_mode"] == "warn"

    def test_sector_at_cap_flagged_true(self, monkeypatch, capsys, params):
        params["risk_guards"]["max_positions_per_sector"] = 2
        _seed_open_position("AAA", "Technology")
        _seed_open_position("BBB", "Technology")

        result = _run_status(monkeypatch, capsys)

        by_sector = result["gate_status"]["sector_concentration"]["by_sector"]
        assert by_sector["Technology"]["at_cap"] is True

    def test_zero_position_sectors_omitted(self, monkeypatch, capsys, params):
        # No positions seeded at all -- by_sector should be empty, not
        # listing every known sector at 0.
        result = _run_status(monkeypatch, capsys)

        assert result["gate_status"]["sector_concentration"]["by_sector"] == {}
        # The cap itself is still visible even with nothing near it.
        assert result["gate_status"]["sector_concentration"]["cap_per_sector"] == 5

    def test_db_failure_degrades_sector_block_without_breaking_status(self, monkeypatch, capsys, params):
        def raise_get_open_positions(*a, **kw):
            raise RuntimeError("simulated DB failure")
        monkeypatch.setattr(executor.trader_db, "get_open_positions", raise_get_open_positions)

        result = _run_status(monkeypatch, capsys)  # must not raise

        assert result["gate_status"]["sector_concentration"]["by_sector"] == {}
        # The rest of status (load-bearing every tick) is unaffected.
        assert result["cash"] == 5000.0


class TestGateStatusDailyOrderCount:
    def test_reflects_state_file(self, monkeypatch, capsys, params):
        state_dir = executor.STATE_DIR
        state_dir.mkdir(parents=True, exist_ok=True)
        today = executor._today_et({})
        executor.DAILY_ORDER_COUNT_PATH.write_text(json.dumps({"date": today, "count": 7}))

        result = _run_status(monkeypatch, capsys)

        daily = result["gate_status"]["daily_order_count"]
        assert daily["count_today"] == 7
        assert daily["threshold"] == 30
        assert daily["at_threshold"] is False

    def test_at_threshold_flagged_true(self, monkeypatch, capsys, params):
        state_dir = executor.STATE_DIR
        state_dir.mkdir(parents=True, exist_ok=True)
        today = executor._today_et({})
        executor.DAILY_ORDER_COUNT_PATH.write_text(json.dumps({"date": today, "count": 30}))

        result = _run_status(monkeypatch, capsys)

        assert result["gate_status"]["daily_order_count"]["at_threshold"] is True

    def test_gate_enabled_reflects_params_toggle(self, monkeypatch, capsys, params):
        # DEFAULT_PARAMS has order_count_audit: False
        result = _run_status(monkeypatch, capsys)
        assert result["gate_status"]["daily_order_count"]["gate_enabled"] is False

        params["guardrail_gates"]["order_count_audit"] = True
        result = _run_status(monkeypatch, capsys)
        assert result["gate_status"]["daily_order_count"]["gate_enabled"] is True


class TestGateStatusMaxPositions:
    def test_reported_even_when_gate_disabled(self, monkeypatch, capsys, params):
        # DEFAULT_PARAMS has max_positions: False (intentionally disabled,
        # per params.json's own _max_positions_note) -- still surfaced
        # read-only so the agent isn't tempted to reason about a cap that
        # doesn't currently apply, rather than it being silently omitted.
        positions_response = [
            {"symbol": "AAA", "qty": "1", "market_value": "10.00", "unrealized_pl": "0",
             "unrealized_plpc": "0", "avg_entry_price": "10.00", "current_price": "10.00"},
        ]
        result = _run_status(monkeypatch, capsys, positions_response=positions_response)

        max_pos = result["gate_status"]["max_positions"]
        assert max_pos["gate_enabled"] is False
        assert max_pos["open_count"] == 1
        assert max_pos["cap"] == 25


class TestGateStatusTierCapacity:
    """2026-08-12: surfaces conviction/long-play slot usage and
    graduation_readiness every tick, same "mechanical, not remembered"
    reasoning as the other gate_status blocks -- closes the gap where real
    trades were landing at 1 share almost universally regardless of tier
    because nothing put open slots in front of the agent automatically."""

    def _params_with_tier_caps(self, params):
        params["risk"]["conviction_play"] = {"max_concurrent_conviction_plays": 5}
        params["risk"]["long_play"] = {"max_concurrent_long_plays": 2}
        return params

    def test_no_positions_reports_full_capacity(self, monkeypatch, capsys, params):
        self._params_with_tier_caps(params)
        result = _run_status(monkeypatch, capsys)

        tier = result["gate_status"]["tier_capacity"]
        assert tier["conviction_play"] == {"open_count": 0, "cap": 5, "slots_available": 5}
        assert tier["long_play"] == {"open_count": 0, "cap": 2, "slots_available": 2}

    def test_open_conviction_and_long_positions_reduce_available_slots(self, monkeypatch, capsys, params):
        self._params_with_tier_caps(params)
        _seed_open_position("AAA", "Financials", play_type="conviction")
        _seed_open_position("BBB", "Financials", play_type="conviction")
        _seed_open_position("CCC", "Healthcare", play_type="long")

        result = _run_status(monkeypatch, capsys)

        tier = result["gate_status"]["tier_capacity"]
        assert tier["conviction_play"] == {"open_count": 2, "cap": 5, "slots_available": 3}
        assert tier["long_play"] == {"open_count": 1, "cap": 2, "slots_available": 1}

    def test_standard_positions_do_not_count_against_either_tier(self, monkeypatch, capsys, params):
        self._params_with_tier_caps(params)
        _seed_open_position("AAA", "Financials")  # play_type defaults to standard

        result = _run_status(monkeypatch, capsys)

        tier = result["gate_status"]["tier_capacity"]
        assert tier["conviction_play"]["open_count"] == 0
        assert tier["long_play"]["open_count"] == 0

    def test_no_cap_configured_reports_none_not_a_crash(self, monkeypatch, capsys, params):
        # DEFAULT_PARAMS has no conviction_play/long_play block at all.
        result = _run_status(monkeypatch, capsys)

        tier = result["gate_status"]["tier_capacity"]
        assert tier["conviction_play"] == {"open_count": 0, "cap": None, "slots_available": None}
        assert tier["long_play"] == {"open_count": 0, "cap": None, "slots_available": None}

    def test_graduation_readiness_included_from_position_sizing(self, monkeypatch, capsys, params):
        import position_sizing
        fake_result = {"ready": True, "hit_rate_bar": 0.70,
                        "qualifying_signals": {"narrative": {"hit_rate": 0.8}},
                        "qualifying_tree_nodes": {}}
        monkeypatch.setattr(position_sizing, "graduation_readiness", lambda: fake_result)

        result = _run_status(monkeypatch, capsys)

        assert result["gate_status"]["tier_capacity"]["graduation_readiness"] == fake_result

    def test_graduation_readiness_failure_degrades_to_none_without_breaking_status(self, monkeypatch, capsys, params):
        import position_sizing

        def raise_error():
            raise RuntimeError("simulated scorecard read failure")
        monkeypatch.setattr(position_sizing, "graduation_readiness", raise_error)

        result = _run_status(monkeypatch, capsys)

        assert result["gate_status"]["tier_capacity"]["graduation_readiness"] is None
        # status itself still succeeded -- best-effort, same discipline as
        # the other fail-open blocks in this action.
        assert "portfolio_value" in result


class TestGateStatusMatchesRealGateMath:
    def test_sector_numbers_match_gate_sector_concentration(self, monkeypatch, capsys, params):
        """Cross-check: gate_status calls the same underlying helpers the
        real gate uses (trader_db.get_open_positions), not a re-derived
        copy, so this should hold structurally -- direct assertion closes
        the loop."""
        _seed_open_position("AAA", "Healthcare")
        params["risk_guards"]["max_positions_per_sector"] = 3

        result = _run_status(monkeypatch, capsys)
        gate_status_count = result["gate_status"]["sector_concentration"]["by_sector"]["Healthcare"]["open"]

        real_gate_count = sum(
            1 for p in [{"symbol": "AAA"}]
            if executor._sector_of(p["symbol"]) == "Healthcare"
        )
        assert gate_status_count == real_gate_count == 1

    def test_order_count_matches_gate_daily_order_count_helper(self, monkeypatch, capsys, params):
        state_dir = executor.STATE_DIR
        state_dir.mkdir(parents=True, exist_ok=True)
        today = executor._today_et({})
        executor.DAILY_ORDER_COUNT_PATH.write_text(json.dumps({"date": today, "count": 12}))

        result = _run_status(monkeypatch, capsys)
        assert result["gate_status"]["daily_order_count"]["count_today"] == executor._load_daily_order_count(today) == 12
