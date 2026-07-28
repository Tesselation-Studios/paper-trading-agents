#!/usr/bin/env python3
"""
Unit tests for scripts/discovery_db.py -- the SQLite candidate-pool store
for discovery_daemon.py's continuous scanner. Real sqlite3 files under
tmp_path, no mocking needed (this module has no network dependency).
"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import discovery_db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = discovery_db.get_conn(tmp_path / "pool.db")
    yield c
    c.close()


class TestInitSchema:
    def test_idempotent(self, conn):
        discovery_db.init_schema(conn)
        discovery_db.init_schema(conn)  # should not raise
        assert discovery_db.pool_stats(conn)["total_tracked"] == 0


class TestUpsertCandidates:
    def test_new_ticker_sets_first_seen_and_screen_count_one(self, conn):
        discovery_db.upsert_candidates(
            conn,
            [{"ticker": "AAA", "price": 10.0, "rsi": 55.0, "volume_ratio": 1.2,
              "macd_hist": 0.1, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T12:00:00Z",
        )
        row = conn.execute("SELECT * FROM candidates WHERE ticker = 'AAA'").fetchone()
        assert row["first_seen_at"] == "2026-07-27T12:00:00Z"
        assert row["screen_count"] == 1
        assert row["in_band"] == 1
        assert row["last_in_band_at"] == "2026-07-27T12:00:00Z"

    def test_reupsert_bumps_last_screened_and_count_preserves_first_seen(self, conn):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T12:00:00Z",
        )
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 11.0, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T14:00:00Z",
        )
        row = conn.execute("SELECT * FROM candidates WHERE ticker = 'AAA'").fetchone()
        assert row["first_seen_at"] == "2026-07-27T12:00:00Z"
        assert row["last_screened_at"] == "2026-07-27T14:00:00Z"
        assert row["screen_count"] == 2
        assert row["price"] == 11.0

    def test_failed_screen_does_not_touch_last_in_band_at(self, conn):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T12:00:00Z",
        )
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 9.0, "in_band": False}],
            universe_generation=1, screened_at="2026-07-27T14:00:00Z",
        )
        row = conn.execute("SELECT * FROM candidates WHERE ticker = 'AAA'").fetchone()
        assert row["last_in_band_at"] == "2026-07-27T12:00:00Z"  # unchanged
        assert row["last_screened_at"] == "2026-07-27T14:00:00Z"  # bumped regardless
        assert row["in_band"] == 0
        assert row["screen_count"] == 2


class TestGetTopCandidates:
    def _seed(self, conn, ticker, in_band, volume_ratio, screened_at):
        discovery_db.upsert_candidates(
            conn, [{"ticker": ticker, "price": 10.0, "volume_ratio": volume_ratio, "in_band": in_band}],
            universe_generation=1, screened_at=screened_at,
        )

    def test_excludes_out_of_band(self, conn):
        self._seed(conn, "IN", True, 1.5, "2026-07-27T12:00:00+00:00")
        self._seed(conn, "OUT", False, 2.0, "2026-07-27T12:00:00+00:00")
        result = discovery_db.get_top_candidates(conn, limit=10, max_age_seconds=86400,
                                                   now="2026-07-27T12:05:00+00:00")
        assert [r["ticker"] for r in result] == ["IN"]

    def test_excludes_stale(self, conn):
        self._seed(conn, "FRESH", True, 1.0, "2026-07-27T12:00:00+00:00")
        self._seed(conn, "STALE", True, 2.0, "2026-07-27T08:00:00+00:00")
        result = discovery_db.get_top_candidates(conn, limit=10, max_age_seconds=3600,
                                                   now="2026-07-27T12:05:00+00:00")
        assert [r["ticker"] for r in result] == ["FRESH"]

    def test_orders_by_volume_ratio_desc_and_respects_limit(self, conn):
        self._seed(conn, "LOW", True, 1.0, "2026-07-27T12:00:00+00:00")
        self._seed(conn, "HIGH", True, 3.0, "2026-07-27T12:00:00+00:00")
        self._seed(conn, "MID", True, 2.0, "2026-07-27T12:00:00+00:00")
        result = discovery_db.get_top_candidates(conn, limit=2, max_age_seconds=86400,
                                                   now="2026-07-27T12:05:00+00:00")
        assert [r["ticker"] for r in result] == ["HIGH", "MID"]


class TestUniverseSnapshot:
    def test_replace_is_atomic(self, conn):
        discovery_db.upsert_universe_snapshot(conn, ["A", "B", "C"], generation=1, fetched_at="t1")
        discovery_db.upsert_universe_snapshot(conn, ["X", "Y"], generation=2, fetched_at="t2")
        assert discovery_db.get_universe_snapshot(conn) == ["X", "Y"]
        assert discovery_db.get_universe_generation(conn) == 2

    def test_generation_none_when_empty(self, conn):
        assert discovery_db.get_universe_generation(conn) is None


class TestPoolStats:
    def test_counts_match_seeded_fixture(self, conn):
        discovery_db.upsert_candidates(
            conn,
            [
                {"ticker": "A", "price": 1.0, "in_band": True},
                {"ticker": "B", "price": 2.0, "in_band": False},
                {"ticker": "C", "price": 3.0, "in_band": True},
            ],
            universe_generation=1, screened_at="2026-07-27T12:00:00+00:00",
        )
        stats = discovery_db.pool_stats(conn, now="2026-07-27T12:05:00+00:00", max_age_seconds=3600)
        assert stats["total_tracked"] == 3
        assert stats["in_band_count"] == 2
        assert stats["fresh_in_band_count"] == 2
        assert stats["most_recent_last_screened_at"] == "2026-07-27T12:00:00+00:00"
