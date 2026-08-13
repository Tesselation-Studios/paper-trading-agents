#!/usr/bin/env python3
"""
Unit tests for scripts/discovery_db.py -- the SQLite candidate-pool store
for discovery_daemon.py's continuous scanner. Real sqlite3 files under
tmp_path, no mocking needed (this module has no network dependency).
"""
import sqlite3
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


class TestCompositeRanking:
    """2026-08-01: ranking was volume_ratio DESC alone, which put whatever
    illiquid name had a one-day volume freak at the top of the promotion
    queue. Composite of volume (saturating) + sentiment magnitude + news
    recency."""

    NOW = "2026-08-01T12:00:00+00:00"

    def _seed(self, conn, ticker, volume_ratio, sentiment=None, news_at=None,
              screened_at="2026-08-01T11:55:00+00:00"):
        discovery_db.upsert_candidates(
            conn, [{"ticker": ticker, "price": 10.0, "volume_ratio": volume_ratio, "in_band": True}],
            universe_generation=1, screened_at=screened_at,
        )
        if news_at is not None:
            discovery_db.record_news_confirmation(conn, ticker, sentiment, "headline", news_at)

    def _order(self, conn):
        return [r["ticker"] for r in discovery_db.get_top_candidates(
            conn, limit=10, max_age_seconds=86400, now=self.NOW)]

    def test_volume_still_orders_a_pool_with_no_news(self, conn):
        """The pure-volume ordering must survive unchanged when nothing has
        sentiment or news yet -- that's the daemon's steady state early in
        a scan cycle."""
        self._seed(conn, "LOW", 1.0)
        self._seed(conn, "HIGH", 3.0)
        self._seed(conn, "MID", 2.0)
        assert self._order(conn) == ["HIGH", "MID", "LOW"]

    def test_volume_saturates_so_news_backed_move_outranks_volume_freak(self, conn):
        """The GFGF-vs-ZEO case: a 17.9x volume freak with a headline that
        scored 0.0 sentiment loses to a smaller move with a real story."""
        self._seed(conn, "FREAK", 17.9, sentiment=0.0, news_at="2026-08-01T11:00:00+00:00")
        self._seed(conn, "REAL", 6.0, sentiment=0.9, news_at="2026-08-01T11:00:00+00:00")
        assert self._order(conn) == ["REAL", "FREAK"]

    def test_bearish_sentiment_counts_by_magnitude(self, conn):
        self._seed(conn, "BEAR", 5.0, sentiment=-0.9, news_at="2026-08-01T11:00:00+00:00")
        self._seed(conn, "FLAT", 5.0, sentiment=0.0, news_at="2026-08-01T11:00:00+00:00")
        assert self._order(conn) == ["BEAR", "FLAT"]

    def test_fresh_news_outranks_week_old_news(self, conn):
        self._seed(conn, "FRESHNEWS", 5.0, sentiment=0.5, news_at="2026-08-01T11:00:00+00:00")
        self._seed(conn, "OLDNEWS", 5.0, sentiment=0.5, news_at="2026-07-25T11:00:00+00:00")
        assert self._order(conn) == ["FRESHNEWS", "OLDNEWS"]

    def test_no_news_scores_below_identical_ticker_with_news(self, conn):
        self._seed(conn, "SILENT", 5.0)
        self._seed(conn, "COVERED", 5.0, sentiment=0.3, news_at="2026-08-01T11:00:00+00:00")
        assert self._order(conn) == ["COVERED", "SILENT"]

    def test_rank_score_exposed_and_bounded(self, conn):
        self._seed(conn, "MAXED", 999.0, sentiment=5.0, news_at=self.NOW)
        row = discovery_db.get_top_candidates(conn, limit=1, max_age_seconds=86400, now=self.NOW)[0]
        # Out-of-range inputs (unbounded volume_ratio, sentiment past 1.0)
        # are clamped, so no one component can run away with the ranking.
        assert row["rank_score"] == pytest.approx(
            discovery_db.RANK_WEIGHT_VOLUME + discovery_db.RANK_WEIGHT_SENTIMENT
            + discovery_db.RANK_WEIGHT_NEWS_RECENCY
        )


class TestFundamentalsEnrichment:
    """2026-08-13: sector/industry/market_cap columns, best-effort
    yfinance enrichment written by discovery_daemon.py."""

    def test_new_columns_null_by_default(self, conn):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00Z",
        )
        row = conn.execute("SELECT * FROM candidates WHERE ticker = 'AAA'").fetchone()
        assert row["sector"] is None
        assert row["industry"] is None
        assert row["market_cap"] is None

    def test_record_fundamentals_writes_all_three(self, conn):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00Z",
        )
        discovery_db.record_fundamentals(conn, "AAA", "Technology", "Software", 5_000_000_000.0)
        row = conn.execute("SELECT * FROM candidates WHERE ticker = 'AAA'").fetchone()
        assert row["sector"] == "Technology"
        assert row["industry"] == "Software"
        assert row["market_cap"] == 5_000_000_000.0

    def test_record_fundamentals_overwrites_not_coalesces(self, conn):
        """Unlike sentiment/news_headline elsewhere, a re-fetch should
        reflect the current read, not freeze the first one forever."""
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00Z",
        )
        discovery_db.record_fundamentals(conn, "AAA", "Technology", "Software", 5_000_000_000.0)
        discovery_db.record_fundamentals(conn, "AAA", None, None, None)
        row = conn.execute("SELECT * FROM candidates WHERE ticker = 'AAA'").fetchone()
        assert row["sector"] is None
        assert row["market_cap"] is None

    def test_migration_adds_columns_to_pre_existing_db(self, tmp_path):
        """A DB created before this column existed must gain it via
        _migrate_add_column, not silently keep the old schema."""
        db_path = tmp_path / "old_pool.db"
        old_conn = sqlite3.connect(str(db_path))
        old_conn.executescript("""
            CREATE TABLE candidates (
                ticker TEXT PRIMARY KEY, price REAL NOT NULL, rsi REAL,
                volume_ratio REAL, macd_hist REAL, in_band INTEGER NOT NULL DEFAULT 0,
                sentiment REAL, news_headline TEXT, news_confirmed_at TEXT,
                first_seen_at TEXT NOT NULL, last_screened_at TEXT NOT NULL,
                last_in_band_at TEXT, screen_count INTEGER NOT NULL DEFAULT 0,
                universe_generation INTEGER NOT NULL
            );
        """)
        old_conn.commit()
        old_conn.close()

        migrated_conn = discovery_db.get_conn(db_path)
        columns = {row[1] for row in migrated_conn.execute("PRAGMA table_info(candidates)")}
        assert {"sector", "industry", "market_cap"} <= columns
        migrated_conn.close()


class TestMaFilingCheck:
    """2026-08-13: BWMN data-gap fix, see scripts/edgar_scan.py."""

    def test_new_columns_null_by_default(self, conn):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00Z",
        )
        row = conn.execute("SELECT * FROM candidates WHERE ticker = 'AAA'").fetchone()
        assert row["ma_filing_flag"] is None
        assert row["ma_filing_checked_at"] is None

    def test_record_ma_filing_check_writes_flag_and_timestamp(self, conn):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00Z",
        )
        discovery_db.record_ma_filing_check(
            conn, "AAA", "8-K item 2.01 filed 2026-08-11", "2026-08-13T12:05:00Z",
        )
        row = conn.execute("SELECT * FROM candidates WHERE ticker = 'AAA'").fetchone()
        assert row["ma_filing_flag"] == "8-K item 2.01 filed 2026-08-11"
        assert row["ma_filing_checked_at"] == "2026-08-13T12:05:00Z"

    def test_clean_check_still_updates_checked_at(self, conn):
        """A ticker with nothing found must not look perpetually
        never-checked -- checked_at is the ordering signal, not the flag."""
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00Z",
        )
        discovery_db.record_ma_filing_check(conn, "AAA", None, "2026-08-13T12:05:00Z")
        row = conn.execute("SELECT * FROM candidates WHERE ticker = 'AAA'").fetchone()
        assert row["ma_filing_flag"] is None
        assert row["ma_filing_checked_at"] == "2026-08-13T12:05:00Z"


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
