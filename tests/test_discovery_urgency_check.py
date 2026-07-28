#!/usr/bin/env python3
"""
Unit tests for scripts/discovery_urgency_check.py — pure logic (candidate
counting, urgency-trigger thresholds). No network — get_account and
discovery_scan's functions are all mocked. trader_db.DB_PATH is
monkeypatched to an isolated tmp_path db in every test (2026-07-28,
migrated off a regex-parsed watchlist.md).
"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import discovery_urgency_check as urgency  # noqa: E402
import trader_db  # noqa: E402


def _seed_candidates(db_path, tickers):
    conn = trader_db.get_conn(db_path)
    for t in tickers:
        trader_db.upsert_watchlist_candidate(conn, ticker=t)
    conn.close()


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")


class TestCountWatchlistCandidates:
    def test_counts_active_candidates(self, tmp_path):
        _seed_candidates(tmp_path / "trader.db", ["AAA", "BBB"])
        conn = trader_db.get_conn(tmp_path / "trader.db")
        try:
            assert urgency.count_watchlist_candidates(conn) == 2
        finally:
            conn.close()

    def test_empty_candidates_no_crash(self, tmp_path):
        conn = trader_db.get_conn(tmp_path / "trader.db")
        try:
            assert urgency.count_watchlist_candidates(conn) == 0
        finally:
            conn.close()

    def test_dropped_candidates_not_counted(self, tmp_path):
        """2026-07-28: dropped rows are actually DELETEd now, not
        struck-through text -- nothing left to skip."""
        db_path = tmp_path / "trader.db"
        _seed_candidates(db_path, ["AAA", "BBB"])
        conn = trader_db.get_conn(db_path)
        trader_db.remove_watchlist_candidate(conn, "BBB")
        assert urgency.count_watchlist_candidates(conn) == 1
        conn.close()

    def test_held_positions_not_counted(self, tmp_path):
        """count_watchlist_candidates only counts the candidate pipeline,
        not held positions -- unlike the old text-parsing version, this is
        structural now (positions/watchlist_candidates are separate
        tables) rather than a section-boundary parse."""
        db_path = tmp_path / "trader.db"
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(conn, ticker="F", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.close()
        _seed_candidates(db_path, ["AAA"])
        conn = trader_db.get_conn(db_path)
        try:
            assert urgency.count_watchlist_candidates(conn) == 1
        finally:
            conn.close()


class TestCheckAndMaybeDiscover:
    @pytest.fixture(autouse=True)
    def isolated_daemon_health(self, monkeypatch):
        """check_and_maybe_discover() now calls discovery_daemon.daemon_health()
        every time (informational output keys, doesn't affect triggered logic)
        -- without isolation these tests would read whatever real
        state/discovery_daemon.json happens to be on disk. Default healthy;
        TestDaemonHealthKeys below overrides per-state."""
        monkeypatch.setattr(urgency.discovery_daemon, "daemon_health",
                             lambda: {"healthy": True, "last_cycle_completed_at": "2026-07-27T12:00:00+00:00",
                                       "last_cycle_status": "ok"})

    def _mock_account(self, monkeypatch, cash, equity):
        monkeypatch.setattr(urgency.executor, "get_account",
                             lambda account: {"cash": str(cash), "equity": str(equity)})

    def test_healthy_deployment_and_pipeline_no_trigger(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=2000, equity=10000)  # 20% cash
        _seed_candidates(tmp_path / "trader.db", ["AAA", "BBB", "CCC"])
        result = urgency.check_and_maybe_discover()
        assert result["triggered"] is False
        assert result["under_deployed"] is False
        assert "discovery_run" not in result

    def test_high_cash_but_healthy_pipeline_no_trigger(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=9500, equity=10000)  # 95% cash
        _seed_candidates(tmp_path / "trader.db", ["AAA", "BBB", "CCC"])
        result = urgency.check_and_maybe_discover()
        assert result["under_deployed"] is True
        assert result["thin_pipeline"] is False
        assert result["triggered"] is False

    def test_high_cash_and_thin_pipeline_triggers_discovery(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=9500, equity=10000)  # 95% cash
        # empty candidates -- nothing seeded

        monkeypatch.setattr(urgency.discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(urgency.discovery_scan, "screen_candidates",
                             lambda: [{"ticker": "AAA", "price": 10.0, "rsi": 55.0,
                                       "volume_ratio": 1.0, "macd_hist": 0.1}])
        monkeypatch.setattr(urgency.discovery_scan, "confirm_with_news", lambda c: c)
        monkeypatch.setattr(urgency.discovery_scan, "write_discoveries_file",
                             lambda *a, **k: tmp_path / "2026-07-23.md")

        result = urgency.check_and_maybe_discover()
        assert result["triggered"] is True
        assert result["discovery_run"]["top_written"] == ["AAA"]

    def test_zero_equity_does_not_crash(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=0, equity=0)
        # non-empty candidates: this test is about the cash_pct=0/equity=0 division
        # guard, not the trigger logic — keep it off the empty-pipeline OR-branch
        # (see test_empty_pipeline_triggers_regardless_of_low_cash for that path)
        _seed_candidates(tmp_path / "trader.db", ["AAA", "BBB", "CCC"])
        result = urgency.check_and_maybe_discover()
        assert result["cash_pct"] == 0.0
        assert result["triggered"] is False

    def test_custom_thresholds_respected(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=5000, equity=10000)  # 50% cash
        _seed_candidates(tmp_path / "trader.db", ["AAA"])  # 1 candidate
        monkeypatch.setattr(urgency.discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(urgency.discovery_scan, "screen_candidates", lambda: [])
        monkeypatch.setattr(urgency.discovery_scan, "confirm_with_news", lambda c: c)
        monkeypatch.setattr(urgency.discovery_scan, "write_discoveries_file",
                             lambda *a, **k: tmp_path / "2026-07-23.md")

        # default thresholds (70%, 3) would not trigger on 50% cash
        result = urgency.check_and_maybe_discover()
        assert result["triggered"] is False
        # a looser cash threshold should trigger (50% cash >= 40% threshold, 1 < 3 candidates)
        result2 = urgency.check_and_maybe_discover(cash_threshold_pct=40.0, min_candidates=3)
        assert result2["under_deployed"] is True
        assert result2["triggered"] is True

    def test_empty_pipeline_triggers_regardless_of_low_cash(self, monkeypatch, tmp_path):
        # 2% cash — heavily deployed, would never trip the cash-gated branch
        self._mock_account(monkeypatch, cash=200, equity=10000)
        # zero candidates seeded

        monkeypatch.setattr(urgency.discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(urgency.discovery_scan, "screen_candidates", lambda: [])
        monkeypatch.setattr(urgency.discovery_scan, "confirm_with_news", lambda c: c)
        monkeypatch.setattr(urgency.discovery_scan, "write_discoveries_file",
                             lambda *a, **k: tmp_path / "2026-07-27.md")

        result = urgency.check_and_maybe_discover()
        assert result["pipeline_empty"] is True
        assert result["under_deployed"] is False
        assert result["triggered"] is True

    def test_one_candidate_does_not_trigger_empty_branch_at_default_floor(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=200, equity=10000)  # still low cash
        _seed_candidates(tmp_path / "trader.db", ["AAA"])  # 1 candidate

        result = urgency.check_and_maybe_discover()
        assert result["pipeline_empty"] is False  # default empty_floor is 0
        assert result["triggered"] is False

    def test_custom_empty_floor_respected(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=200, equity=10000)
        _seed_candidates(tmp_path / "trader.db", ["AAA"])  # 1 candidate

        monkeypatch.setattr(urgency.discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(urgency.discovery_scan, "screen_candidates", lambda: [])
        monkeypatch.setattr(urgency.discovery_scan, "confirm_with_news", lambda c: c)
        monkeypatch.setattr(urgency.discovery_scan, "write_discoveries_file",
                             lambda *a, **k: tmp_path / "2026-07-27.md")

        result = urgency.check_and_maybe_discover(empty_floor=1)
        assert result["pipeline_empty"] is True
        assert result["triggered"] is True

    def test_params_json_defaults_used_when_no_override(self, monkeypatch, tmp_path):
        params_file = tmp_path / "params.json"
        params_file.write_text('{"watchlist": {"discovery_urgency": {"empty_floor": 2}}}')
        monkeypatch.setattr(urgency, "PARAMS_PATH", params_file)

        self._mock_account(monkeypatch, cash=200, equity=10000)
        _seed_candidates(tmp_path / "trader.db", ["AAA", "BBB"])  # 2 candidates

        monkeypatch.setattr(urgency.discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(urgency.discovery_scan, "screen_candidates", lambda: [])
        monkeypatch.setattr(urgency.discovery_scan, "confirm_with_news", lambda c: c)
        monkeypatch.setattr(urgency.discovery_scan, "write_discoveries_file",
                             lambda *a, **k: tmp_path / "2026-07-27.md")

        # no explicit empty_floor kwarg — must come from the monkeypatched params.json
        result = urgency.check_and_maybe_discover()
        assert result["pipeline_empty"] is True
        assert result["triggered"] is True


class TestDaemonHealthKeys:
    """daemon_healthy/daemon_last_cycle_completed_at are purely
    informational -- confirms all three states surface correctly without
    ever changing triggered/under_deployed/thin_pipeline/pipeline_empty."""

    def _mock_account(self, monkeypatch, cash, equity):
        monkeypatch.setattr(urgency.executor, "get_account",
                             lambda account: {"cash": str(cash), "equity": str(equity)})

    def _healthy_watchlist(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=2000, equity=10000)  # 20% cash, no trigger
        _seed_candidates(tmp_path / "trader.db", ["AAA", "BBB", "CCC"])

    def test_daemon_missing_reports_unhealthy(self, monkeypatch, tmp_path):
        self._healthy_watchlist(monkeypatch, tmp_path)
        monkeypatch.setattr(urgency.discovery_daemon, "daemon_health",
                             lambda: {"healthy": False, "last_cycle_completed_at": None, "last_cycle_status": None})
        result = urgency.check_and_maybe_discover()
        assert result["daemon_healthy"] is False
        assert result["daemon_last_cycle_completed_at"] is None
        assert result["triggered"] is False  # unaffected by daemon health

    def test_daemon_fresh_reports_healthy(self, monkeypatch, tmp_path):
        self._healthy_watchlist(monkeypatch, tmp_path)
        monkeypatch.setattr(urgency.discovery_daemon, "daemon_health",
                             lambda: {"healthy": True, "last_cycle_completed_at": "2026-07-27T12:00:00+00:00",
                                       "last_cycle_status": "ok"})
        result = urgency.check_and_maybe_discover()
        assert result["daemon_healthy"] is True
        assert result["daemon_last_cycle_completed_at"] == "2026-07-27T12:00:00+00:00"

    def test_daemon_stale_reports_unhealthy(self, monkeypatch, tmp_path):
        self._healthy_watchlist(monkeypatch, tmp_path)
        monkeypatch.setattr(urgency.discovery_daemon, "daemon_health",
                             lambda: {"healthy": False, "last_cycle_completed_at": "2020-01-01T00:00:00+00:00",
                                       "last_cycle_status": "ok"})
        result = urgency.check_and_maybe_discover()
        assert result["daemon_healthy"] is False
        assert result["daemon_last_cycle_completed_at"] == "2020-01-01T00:00:00+00:00"
