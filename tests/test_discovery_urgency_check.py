#!/usr/bin/env python3
"""
Unit tests for scripts/discovery_urgency_check.py — pure logic (candidate
counting, urgency-trigger thresholds). No network — get_account and
discovery_scan's functions are all mocked.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import discovery_urgency_check as urgency  # noqa: E402


class TestCountWatchlistCandidates:
    def test_counts_active_candidates(self):
        text = "## Currently Held\n- F\n## Candidates\n- AAA — idle_ticks: 0\n- BBB — idle_ticks: 2\n"
        assert urgency.count_watchlist_candidates(text) == 2

    def test_excludes_struck_through(self):
        text = "## Candidates\n- AAA — idle_ticks: 0\n- ~~BBB~~ — dropped\n"
        assert urgency.count_watchlist_candidates(text) == 1

    def test_stops_at_next_section(self):
        text = "## Candidates\n- AAA — idle_ticks: 0\n## Notes\n- BBB should not count\n"
        assert urgency.count_watchlist_candidates(text) == 1

    def test_empty_candidates_section(self):
        text = "## Currently Held\n- F\n## Candidates\n"
        assert urgency.count_watchlist_candidates(text) == 0

    def test_no_candidates_section_at_all(self):
        assert urgency.count_watchlist_candidates("# Watchlist\nsome text\n") == 0

    def test_currently_held_not_counted(self):
        text = "## Currently Held\n- F — open position\n- WSC — open position\n## Candidates\n"
        assert urgency.count_watchlist_candidates(text) == 0


class TestCheckAndMaybeDiscover:
    def _mock_account(self, monkeypatch, cash, equity):
        monkeypatch.setattr(urgency.executor, "get_account",
                             lambda account: {"cash": str(cash), "equity": str(equity)})

    def test_healthy_deployment_and_pipeline_no_trigger(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=2000, equity=10000)  # 20% cash
        watchlist = tmp_path / "watchlist.md"
        watchlist.write_text("## Candidates\n- AAA\n- BBB\n- CCC\n")
        monkeypatch.setattr(urgency, "WATCHLIST_PATH", watchlist)
        result = urgency.check_and_maybe_discover()
        assert result["triggered"] is False
        assert result["under_deployed"] is False
        assert "discovery_run" not in result

    def test_high_cash_but_healthy_pipeline_no_trigger(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=9500, equity=10000)  # 95% cash
        watchlist = tmp_path / "watchlist.md"
        watchlist.write_text("## Candidates\n- AAA\n- BBB\n- CCC\n")
        monkeypatch.setattr(urgency, "WATCHLIST_PATH", watchlist)
        result = urgency.check_and_maybe_discover()
        assert result["under_deployed"] is True
        assert result["thin_pipeline"] is False
        assert result["triggered"] is False

    def test_high_cash_and_thin_pipeline_triggers_discovery(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=9500, equity=10000)  # 95% cash
        watchlist = tmp_path / "watchlist.md"
        watchlist.write_text("## Candidates\n")  # empty
        monkeypatch.setattr(urgency, "WATCHLIST_PATH", watchlist)

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
        watchlist = tmp_path / "watchlist.md"
        watchlist.write_text("## Candidates\n")
        monkeypatch.setattr(urgency, "WATCHLIST_PATH", watchlist)
        result = urgency.check_and_maybe_discover()
        assert result["cash_pct"] == 0.0

    def test_custom_thresholds_respected(self, monkeypatch, tmp_path):
        self._mock_account(monkeypatch, cash=5000, equity=10000)  # 50% cash
        watchlist = tmp_path / "watchlist.md"
        watchlist.write_text("## Candidates\n- AAA\n")  # 1 candidate
        monkeypatch.setattr(urgency, "WATCHLIST_PATH", watchlist)
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
