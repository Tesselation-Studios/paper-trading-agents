#!/usr/bin/env python3
"""
Unit tests for scripts/discovery_daemon.py -- the pure cursor/chunk math,
DB-facing cycle logic, and health check. No network -- screen_tickers/
fetch_broad_universe/confirm_with_news are all monkeypatched, matching
every other script's no-network test convention in this repo. The
daemon's outer while-True loop itself is NOT tested, matching this repo's
precedent (discovery_scan.main()/discovery_urgency_check.main() don't
test their own entrypoint loops either) -- only the functions it calls.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import discovery_daemon  # noqa: E402
import discovery_db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = discovery_db.get_conn(tmp_path / "pool.db")
    yield c
    c.close()


class TestAdvanceCursor:
    def test_normal_advance(self):
        pos, wrapped = discovery_daemon.advance_cursor(0, 10, 100)
        assert pos == 10
        assert wrapped is False

    def test_wraps_at_end(self):
        pos, wrapped = discovery_daemon.advance_cursor(95, 10, 100)
        assert pos == 5
        assert wrapped is True

    def test_exact_boundary_wraps(self):
        pos, wrapped = discovery_daemon.advance_cursor(90, 10, 100)
        assert pos == 0
        assert wrapped is True

    def test_chunk_size_ge_universe_size(self):
        pos, wrapped = discovery_daemon.advance_cursor(0, 200, 50)
        assert pos == 0
        assert wrapped is True

    def test_empty_universe(self):
        pos, wrapped = discovery_daemon.advance_cursor(0, 10, 0)
        assert pos == 0
        assert wrapped is False


class TestGetChunk:
    def test_normal_slice(self):
        universe = list("ABCDE")
        assert discovery_daemon.get_chunk(universe, 1, 2) == ["B", "C"]

    def test_wraps_mid_chunk(self):
        universe = list("ABCDE")
        assert discovery_daemon.get_chunk(universe, 3, 4) == ["D", "E", "A", "B"]

    def test_oversized_chunk_returns_whole_universe(self):
        universe = list("ABC")
        assert discovery_daemon.get_chunk(universe, 0, 10) == ["A", "B", "C"]

    def test_empty_universe(self):
        assert discovery_daemon.get_chunk([], 0, 10) == []


class TestRefreshUniverseIfStale:
    def test_noop_when_fresh(self, conn, monkeypatch):
        calls = []
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe",
                             lambda **k: calls.append(1) or ["A", "B"])
        discovery_db.upsert_universe_snapshot(conn, ["X", "Y"], generation=1, fetched_at="2026-07-27T12:00:00+00:00")
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["universe_last_refreshed_at"] = "2026-07-27T12:00:00+00:00"
        cursor_state["universe_generation"] = 1
        universe, new_state, refreshed = discovery_daemon.refresh_universe_if_stale(
            conn, cursor_state, refresh_interval_seconds=86400, now="2026-07-27T12:05:00+00:00",
        )
        assert refreshed is False
        assert universe == ["X", "Y"]
        assert calls == []

    def test_refetches_when_stale(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe",
                             lambda **k: ["NEW1", "NEW2"])
        discovery_db.upsert_universe_snapshot(conn, ["OLD"], generation=1, fetched_at="2026-07-27T00:00:00+00:00")
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["universe_last_refreshed_at"] = "2026-07-27T00:00:00+00:00"
        cursor_state["universe_generation"] = 1
        universe, new_state, refreshed = discovery_daemon.refresh_universe_if_stale(
            conn, cursor_state, refresh_interval_seconds=3600, now="2026-07-27T12:00:00+00:00",
        )
        assert refreshed is True
        assert universe == ["NEW1", "NEW2"]
        assert new_state["universe_generation"] == 2
        assert discovery_db.get_universe_snapshot(conn) == ["NEW1", "NEW2"]

    def test_force_refetches_even_if_fresh(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe", lambda **k: ["A"])
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["universe_last_refreshed_at"] = "2026-07-27T12:00:00+00:00"
        cursor_state["universe_generation"] = 1
        universe, new_state, refreshed = discovery_daemon.refresh_universe_if_stale(
            conn, cursor_state, refresh_interval_seconds=86400, now="2026-07-27T12:00:01+00:00", force=True,
        )
        assert refreshed is True

    def test_generation_increments_only_on_real_refresh(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe", lambda **k: ["A"])
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        discovery_db.upsert_universe_snapshot(conn, ["A"], generation=1, fetched_at="2026-07-27T12:00:00+00:00")
        cursor_state["universe_last_refreshed_at"] = "2026-07-27T12:00:00+00:00"
        cursor_state["universe_generation"] = 1
        _, new_state, refreshed = discovery_daemon.refresh_universe_if_stale(
            conn, cursor_state, refresh_interval_seconds=86400, now="2026-07-27T12:05:00+00:00",
        )
        assert refreshed is False
        assert new_state["universe_generation"] == 1  # unchanged, not incremented


class TestRunCycle:
    def test_correct_chunk_screened_and_upserted(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe",
                             lambda **k: ["A", "B", "C", "D"])
        monkeypatch.setattr(discovery_daemon.discovery_scan, "get_universe_price_band", lambda: (1.0, 100.0))
        seen_chunks = []

        def fake_screen(tickers, min_price, max_price, return_all=False):
            seen_chunks.append(list(tickers))
            return [
                {"ticker": t, "price": 10.0, "rsi": 55.0, "volume_ratio": 1.0, "macd_hist": 0.1, "in_band": True}
                for t in tickers
            ]
        monkeypatch.setattr(discovery_daemon.discovery_screen, "screen_tickers", fake_screen)

        config = dict(discovery_daemon.DEFAULTS)
        config["chunk_size"] = 2
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state = discovery_daemon.run_cycle(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")

        assert seen_chunks == [["A", "B"]]
        assert cursor_state["last_cycle_status"] == "ok"
        assert cursor_state["cursor_position"] == 2
        assert cursor_state["cycles_completed_lifetime"] == 1
        stats = discovery_db.pool_stats(conn)
        assert stats["total_tracked"] == 2

    def test_cursor_persisted_across_calls(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe",
                             lambda **k: ["A", "B", "C", "D"])
        monkeypatch.setattr(discovery_daemon.discovery_scan, "get_universe_price_band", lambda: (1.0, 100.0))
        monkeypatch.setattr(discovery_daemon.discovery_screen, "screen_tickers",
                             lambda tickers, min_price, max_price, return_all=False: [
                                 {"ticker": t, "price": 10.0, "in_band": True} for t in tickers
                             ])
        config = dict(discovery_daemon.DEFAULTS)
        config["chunk_size"] = 2
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state = discovery_daemon.run_cycle(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")
        assert cursor_state["cursor_position"] == 2
        cursor_state = discovery_daemon.run_cycle(conn, cursor_state, config, now="2026-07-27T12:02:00+00:00")
        assert cursor_state["cursor_position"] == 0  # wrapped after covering all 4
        assert cursor_state["full_passes_completed"] == 1

    def test_empty_universe_reports_status_without_crashing(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe", lambda **k: [])
        monkeypatch.setattr(discovery_daemon.discovery_scan, "get_universe_price_band", lambda: (1.0, 100.0))
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state = discovery_daemon.run_cycle(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")
        assert cursor_state["last_cycle_status"] == "no_universe"


class TestMaybeConfirmNews:
    def test_noop_before_interval_elapsed(self, conn, monkeypatch):
        calls = []
        monkeypatch.setattr(discovery_daemon.discovery_scan, "confirm_with_news",
                             lambda candidates, top_n=6: calls.append(1) or candidates)
        config = dict(discovery_daemon.DEFAULTS)
        config["finbert_confirm_interval_seconds"] = 1800
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["last_finbert_confirmation_at"] = "2026-07-27T12:00:00+00:00"
        discovery_daemon.maybe_confirm_news(conn, cursor_state, config, now="2026-07-27T12:10:00+00:00")
        assert calls == []

    def test_runs_and_records_after_interval(self, conn, monkeypatch):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "volume_ratio": 1.5, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T12:00:00+00:00",
        )
        monkeypatch.setattr(discovery_daemon.discovery_scan, "confirm_with_news",
                             lambda candidates, top_n=6: [
                                 dict(c, sentiment=0.4, news_headline="AAA news") for c in candidates
                             ])
        config = dict(discovery_daemon.DEFAULTS)
        config["finbert_confirm_interval_seconds"] = 1800
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["last_finbert_confirmation_at"] = "2026-07-27T11:00:00+00:00"
        new_state = discovery_daemon.maybe_confirm_news(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")
        assert new_state["last_finbert_confirmation_at"] == "2026-07-27T12:00:00+00:00"
        row = conn.execute("SELECT sentiment, news_headline FROM candidates WHERE ticker='AAA'").fetchone()
        assert row["sentiment"] == 0.4
        assert row["news_headline"] == "AAA news"

    def test_exception_from_confirm_with_news_does_not_propagate(self, conn, monkeypatch):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "volume_ratio": 1.5, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T12:00:00+00:00",
        )

        def boom(candidates, top_n=6):
            raise ConnectionError("legend-of-macs.local unreachable")
        monkeypatch.setattr(discovery_daemon.discovery_scan, "confirm_with_news", boom)
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        # should not raise
        new_state = discovery_daemon.maybe_confirm_news(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")
        assert new_state["last_finbert_confirmation_at"] == "2026-07-27T12:00:00+00:00"


class TestDaemonHealth:
    def test_missing_file_unhealthy(self, tmp_path, monkeypatch):
        result = discovery_daemon.daemon_health(cursor_state_path=tmp_path / "missing.json")
        assert result["healthy"] is False

    def test_fresh_is_healthy(self, tmp_path):
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["last_cycle_completed_at"] = "2026-07-27T12:00:00+00:00"
        state["last_cycle_status"] = "ok"
        discovery_daemon.save_cursor_state(state, path=path)
        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now="2026-07-27T12:05:00+00:00", cursor_state_path=path,
        )
        assert result["healthy"] is True

    def test_stale_past_threshold_unhealthy(self, tmp_path):
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["last_cycle_completed_at"] = "2026-07-27T12:00:00+00:00"
        state["last_cycle_status"] = "ok"
        discovery_daemon.save_cursor_state(state, path=path)
        result = discovery_daemon.daemon_health(
            max_staleness_seconds=60, now="2026-07-27T12:05:00+00:00", cursor_state_path=path,
        )
        assert result["healthy"] is False


class TestCursorStatePersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["cursor_position"] = 42
        discovery_daemon.save_cursor_state(state, path=path)
        loaded = discovery_daemon.load_cursor_state(path=path)
        assert loaded["cursor_position"] == 42

    def test_load_missing_file_returns_defaults(self, tmp_path):
        loaded = discovery_daemon.load_cursor_state(path=tmp_path / "nope.json")
        assert loaded == discovery_daemon.DEFAULT_CURSOR_STATE
