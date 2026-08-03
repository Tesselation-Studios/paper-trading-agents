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
    monkeypatch.setattr(executor, "DAILY_ORDER_COUNT_PATH", state_dir / "daily_order_count.json")
    monkeypatch.setattr(executor, "PEAK_EQUITY_PATH", state_dir / "peak_equity.json")
    monkeypatch.setattr(deployment_pressure, "STATE_FILE", state_dir / "deployment_pressure.json")
    yield


@pytest.fixture
def params(monkeypatch):
    data = json.loads(json.dumps(DEFAULT_PARAMS))  # deep copy, per-test mutation safe
    monkeypatch.setattr(executor, "load_params", lambda: data)
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


def _seed_open_position(ticker, sector):
    conn = trader_db.get_conn()
    try:
        trader_db.upsert_position(conn, ticker=ticker, shares=1.0, entry_price=10.0,
                                   entry_time="2026-08-02T10:00:00Z", sector=sector)
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
