#!/usr/bin/env python3
"""
Unit tests for scripts/trader_db.py -- local SQLite trading-state store
(decisions/journal/training_examples/news_cache/alpaca_audit_log). Real
sqlite3 files under tmp_path, no mocking needed. Lands unwired (2026-07-28,
Phase 1 of the local-DB migration) -- nothing imports this module yet.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import trader_db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = trader_db.get_conn(tmp_path / "trader.db")
    yield c
    c.close()


class TestInitSchema:
    def test_idempotent(self, conn):
        trader_db.init_schema(conn)
        trader_db.init_schema(conn)  # should not raise
        for table in ("decisions", "journal", "training_examples", "news_cache", "alpaca_audit_log"):
            assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0


class TestDecisions:
    def test_insert_returns_id(self, conn):
        decision_id = trader_db.insert_decision(
            conn, ticker="AAA", timestamp="2026-07-28T12:00:00Z", decision="BUY",
            conviction=0.6, rationale="momentum entry", regime="momentum_bull",
        )
        assert decision_id is not None
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        assert row["ticker"] == "AAA"
        assert row["decision"] == "BUY"
        assert row["conviction"] == 0.6


class TestJournal:
    def test_insert_returns_id(self, conn):
        journal_id = trader_db.insert_journal_entry(
            conn, timestamp="2026-07-28T12:00:00Z", ticker="AAA", decision="BUY",
            rationale="entry", equity=10000.0, drawdown_pct=0.0,
        )
        assert journal_id is not None

    def test_duplicate_timestamp_conflict_does_nothing(self, conn):
        first = trader_db.insert_journal_entry(conn, timestamp="2026-07-28T12:00:00Z", ticker="AAA")
        second = trader_db.insert_journal_entry(conn, timestamp="2026-07-28T12:00:00Z", ticker="BBB")
        assert first is not None
        assert second is None
        rows = conn.execute("SELECT * FROM journal").fetchall()
        assert len(rows) == 1
        assert rows[0]["ticker"] == "AAA"


class TestTrainingExamples:
    def test_insert_and_label(self, conn):
        decision_id = trader_db.insert_decision(
            conn, ticker="AAA", timestamp="2026-07-28T12:00:00Z", decision="BUY",
        )
        te_id = trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", decision_id=decision_id,
            created_at="2026-07-28T12:00:00Z",
        )
        assert te_id is not None
        row = conn.execute("SELECT * FROM training_examples WHERE id = ?", (te_id,)).fetchone()
        assert row["label_win"] is None

        trader_db.label_training_example(conn, te_id, trade_id="t1", label_win=1, label_return_pct=4.2)
        row = conn.execute("SELECT * FROM training_examples WHERE id = ?", (te_id,)).fetchone()
        assert row["label_win"] == 1
        assert row["label_return_pct"] == 4.2
        assert row["label_horizon"] == "trade_close"

    def test_latest_unlabeled_returns_none_when_all_labeled(self, conn):
        te_id = trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", created_at="2026-07-28T12:00:00Z",
        )
        assert trader_db.latest_unlabeled_training_example(conn, "AAA") == te_id
        trader_db.label_training_example(conn, te_id, trade_id="t1", label_win=1, label_return_pct=1.0)
        assert trader_db.latest_unlabeled_training_example(conn, "AAA") is None

    def test_fetch_labeled_examples_excludes_unlabeled(self, conn):
        unlabeled = trader_db.insert_training_example(
            conn, ticker="AAA", features='{"technical": {"direction": "bullish"}}',
            created_at="2026-07-28T12:00:00Z",
        )
        labeled = trader_db.insert_training_example(
            conn, ticker="BBB", features='{"technical": {"direction": "bearish"}}',
            created_at="2026-07-28T12:00:00Z",
        )
        trader_db.label_training_example(conn, labeled, trade_id="t1", label_win=0, label_return_pct=-2.0)
        result = trader_db.fetch_labeled_training_examples(conn)
        assert len(result) == 1
        assert result[0]["label_win"] == 0


class TestNewsCache:
    def test_upsert_new_articles_returns_inserted_count(self, conn):
        n = trader_db.upsert_news_articles(conn, [
            {"url": "http://a", "title": "A", "source": "rss", "published_at": "2026-07-28T12:00:00Z",
             "collected_at": "2026-07-28T12:00:00Z", "tickers": '["AAA"]', "sentiment_score": 0.5},
        ])
        assert n == 1

    def test_duplicate_url_not_reinserted(self, conn):
        article = {"url": "http://a", "title": "A", "source": "rss", "published_at": "2026-07-28T12:00:00Z",
                    "collected_at": "2026-07-28T12:00:00Z", "tickers": '["AAA"]', "sentiment_score": 0.5}
        trader_db.upsert_news_articles(conn, [article])
        n = trader_db.upsert_news_articles(conn, [article])
        assert n == 0
        rows = conn.execute("SELECT COUNT(*) AS n FROM news_cache").fetchone()
        assert rows["n"] == 1

    def test_recent_watchlist_articles_filters_by_ticker_and_time(self, conn):
        trader_db.upsert_news_articles(conn, [
            {"url": "http://a", "title": "AAA news", "source": "rss", "published_at": "2026-07-28T11:00:00Z",
             "collected_at": "2026-07-28T11:00:00Z", "tickers": '["AAA"]', "sentiment_score": 0.5},
            {"url": "http://b", "title": "unrelated", "source": "rss", "published_at": "2026-07-28T11:00:00Z",
             "collected_at": "2026-07-28T11:00:00Z", "tickers": '["ZZZ"]', "sentiment_score": 0.1},
            {"url": "http://c", "title": "old AAA news", "source": "rss", "published_at": "2026-07-01T11:00:00Z",
             "collected_at": "2026-07-01T11:00:00Z", "tickers": '["AAA"]', "sentiment_score": 0.2},
        ])
        result = trader_db.recent_watchlist_articles(conn, ["AAA"], hours=24, now="2026-07-28T12:00:00Z")
        assert len(result) == 1
        assert result[0]["title"] == "AAA news"


class TestAlpacaAuditLog:
    def test_insert_returns_id(self, conn):
        row_id = trader_db.insert_alpaca_audit_row(
            conn, timestamp="2026-07-28T12:00:00Z", endpoint="orders", method="POST",
            request_summary='{"ticker": "AAA", "qty": 1}', status_code=200,
        )
        assert row_id is not None

    def test_prune_removes_old_rows_only(self, conn):
        trader_db.insert_alpaca_audit_row(conn, timestamp="2026-01-01T00:00:00Z", endpoint="orders", method="GET")
        trader_db.insert_alpaca_audit_row(conn, timestamp="2026-07-28T00:00:00Z", endpoint="orders", method="GET")
        deleted = trader_db.prune_alpaca_audit_log(conn, retention_days=90, now="2026-07-28T12:00:00Z")
        assert deleted == 1
        remaining = conn.execute("SELECT COUNT(*) AS n FROM alpaca_audit_log").fetchone()["n"]
        assert remaining == 1
