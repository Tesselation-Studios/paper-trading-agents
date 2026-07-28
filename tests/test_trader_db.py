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


class TestPositions:
    def test_upsert_creates_open_position(self, conn):
        trader_db.upsert_position(
            conn, ticker="AAA", shares=1.0, entry_price=30.98, entry_time="2026-07-24T10:05:00Z",
            sector="Technology", thesis="momentum entry, RSI 58",
        )
        row = trader_db.get_position(conn, "AAA")
        assert row["status"] == "open"
        assert row["shares"] == 1.0
        assert row["thesis"] == "momentum entry, RSI 58"

    def test_upsert_scale_in_updates_shares_keeps_entry_price(self, conn):
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=30.98, entry_time="t1")
        trader_db.upsert_position(conn, ticker="AAA", shares=4.0, entry_price=30.98, entry_time="t1")
        row = trader_db.get_position(conn, "AAA")
        assert row["shares"] == 4.0

    def test_upsert_without_thesis_preserves_existing(self, conn):
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1", thesis="original")
        trader_db.upsert_position(conn, ticker="AAA", shares=2.0, entry_price=10.0, entry_time="t1")
        row = trader_db.get_position(conn, "AAA")
        assert row["thesis"] == "original"

    def test_close_position_sets_status_and_realized_fields(self, conn):
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.close_position(
            conn, ticker="AAA", closed_at="2026-07-28T10:00:00Z", close_reason="trailing stop breach -5.7%",
            realized_pnl=-1.83, realized_return_pct=-5.7,
        )
        row = trader_db.get_position(conn, "AAA")
        assert row["status"] == "closed"
        assert row["realized_pnl"] == -1.83

    def test_get_open_positions_excludes_closed(self, conn):
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.upsert_position(conn, ticker="BBB", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.close_position(conn, ticker="BBB", closed_at="t2", close_reason="exit", realized_pnl=1.0, realized_return_pct=1.0)
        open_tickers = [p["ticker"] for p in trader_db.get_open_positions(conn)]
        assert open_tickers == ["AAA"]

    def test_get_all_positions_includes_open_and_closed(self, conn):
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.upsert_position(conn, ticker="BBB", shares=1.0, entry_price=10.0, entry_time="t2")
        trader_db.close_position(conn, ticker="BBB", closed_at="t3", close_reason="exit", realized_pnl=1.0, realized_return_pct=1.0)
        all_tickers = {p["ticker"] for p in trader_db.get_all_positions(conn)}
        assert all_tickers == {"AAA", "BBB"}


class TestWatchlistCandidates:
    def test_upsert_new_candidate_idle_ticks_zero(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="LDRX", source="discovery_pool gen 2")
        row = conn.execute("SELECT * FROM watchlist_candidates WHERE ticker = 'LDRX'").fetchone()
        assert row["idle_ticks"] == 0

    def test_increment_idle_ticks_bumps_all(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        trader_db.increment_idle_ticks(conn)
        trader_db.increment_idle_ticks(conn)
        rows = {r["ticker"]: r["idle_ticks"] for r in trader_db.get_watchlist_candidates(conn)}
        assert rows == {"AAA": 2, "BBB": 2}

    def test_increment_idle_ticks_skips_touched(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        trader_db.increment_idle_ticks(conn, except_tickers=["AAA"])
        rows = {r["ticker"]: r["idle_ticks"] for r in trader_db.get_watchlist_candidates(conn)}
        assert rows == {"AAA": 0, "BBB": 1}

    def test_touch_resets_idle_ticks(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.increment_idle_ticks(conn)
        trader_db.increment_idle_ticks(conn)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA", note="touched again")
        row = conn.execute("SELECT * FROM watchlist_candidates WHERE ticker = 'AAA'").fetchone()
        assert row["idle_ticks"] == 0
        assert row["note"] == "touched again"

    def test_drop_stale_removes_and_returns_dropped(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        for _ in range(24):
            trader_db.increment_idle_ticks(conn, except_tickers=["BBB"])
        dropped = trader_db.drop_stale_watchlist_candidates(conn, idle_ticks_threshold=24)
        assert dropped == ["AAA"]
        remaining = [r["ticker"] for r in trader_db.get_watchlist_candidates(conn)]
        assert remaining == ["BBB"]

    def test_remove_watchlist_candidate(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.remove_watchlist_candidate(conn, "AAA")
        assert trader_db.get_watchlist_candidates(conn) == []


class TestBankrollState:
    def test_get_missing_state_returns_none(self, conn):
        assert trader_db.get_bankroll_state(conn) is None

    def test_upsert_and_get(self, conn):
        trader_db.upsert_bankroll_state(
            conn, ceiling=700.0, growth_rate=0.02, decay_rate=0.01, target_profit_pct=0.01,
            lifetime_trades=24, lifetime_net_pnl=7.31,
        )
        state = trader_db.get_bankroll_state(conn)
        assert state["ceiling"] == 700.0
        assert state["lifetime_trades"] == 24

    def test_upsert_is_idempotent_singleton(self, conn):
        trader_db.upsert_bankroll_state(conn, ceiling=350.0, growth_rate=0.02, decay_rate=0.01, target_profit_pct=0.01)
        trader_db.upsert_bankroll_state(conn, ceiling=700.0, growth_rate=0.02, decay_rate=0.01, target_profit_pct=0.01)
        rows = conn.execute("SELECT COUNT(*) AS n FROM bankroll_state").fetchone()
        assert rows["n"] == 1
        assert trader_db.get_bankroll_state(conn)["ceiling"] == 700.0

    def test_history_recorded_and_fetched_most_recent_first(self, conn):
        trader_db.record_bankroll_history(conn, timestamp="2026-07-28T10:00:00Z", label="WIN", pnl=1.19, ceiling_after=356.71)
        trader_db.record_bankroll_history(conn, timestamp="2026-07-28T11:00:00Z", label="LOSS", pnl=-1.83, ceiling_after=339.61)
        history = trader_db.get_bankroll_history(conn)
        assert history[0]["label"] == "LOSS"
        assert len(history) == 2


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
