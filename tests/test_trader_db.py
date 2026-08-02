#!/usr/bin/env python3
"""
Unit tests for scripts/trader_db.py -- local SQLite trading-state store
(decisions/journal/training_examples/news_cache/alpaca_audit_log). Real
sqlite3 files under tmp_path, no mocking needed. Lands unwired (2026-07-28,
Phase 1 of the local-DB migration) -- nothing imports this module yet.
"""
import sqlite3
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


class TestMigrateAddColumn:
    """2026-07-30: _migrate_add_column() interpolates table/column/
    coltype_and_default directly into SQL (unavoidable -- SQL can't bind
    identifiers as parameters), so it validates each against a strict
    allowlist first and raises rather than execute anything else. The only
    real caller passes hardcoded literals; these are the defense-in-depth
    boundary checks."""

    def test_valid_call_adds_column(self, conn):
        conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        trader_db._migrate_add_column(conn, "widgets", "note", "TEXT")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(widgets)")}
        assert "note" in columns

    def test_rejects_unsafe_table_name(self, conn):
        with pytest.raises(ValueError):
            trader_db._migrate_add_column(conn, "widgets; DROP TABLE widgets;--", "note", "TEXT")

    def test_rejects_unsafe_column_name(self, conn):
        conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        with pytest.raises(ValueError):
            trader_db._migrate_add_column(conn, "widgets", "note; DROP TABLE widgets;--", "TEXT")

    def test_rejects_unsafe_coltype(self, conn):
        conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
        with pytest.raises(ValueError):
            trader_db._migrate_add_column(conn, "widgets", "note", "TEXT; DROP TABLE widgets;--")


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

    def test_label_horizon_defaults_to_trade_close(self, conn):
        te_id = trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", created_at="2026-07-28T12:00:00Z",
        )
        trader_db.label_training_example(conn, te_id, trade_id="t1", label_win=1, label_return_pct=4.2)
        row = conn.execute("SELECT label_horizon FROM training_examples WHERE id = ?", (te_id,)).fetchone()
        assert row["label_horizon"] == "trade_close"

    def test_label_horizon_accepts_long_play_prediction(self, conn):
        """2026-07-30: a long play's predicted_by_date resolution labels
        with label_horizon='long_play_prediction' instead of the default
        'trade_close' -- a different kind of label (was the prediction
        right, not necessarily tied to an actual realized trade)."""
        te_id = trader_db.insert_training_example(
            conn, ticker="BVS", features="{}", created_at="2026-07-28T12:00:00Z",
        )
        trader_db.label_training_example(
            conn, te_id, trade_id=None, label_win=1, label_return_pct=8.0,
            label_horizon="long_play_prediction",
        )
        row = conn.execute("SELECT label_horizon FROM training_examples WHERE id = ?", (te_id,)).fetchone()
        assert row["label_horizon"] == "long_play_prediction"

    def test_fetch_labeled_examples_filters_by_label_horizon(self, conn):
        trade_close = trader_db.insert_training_example(
            conn, ticker="AAA", features='{"technical": {"direction": "bullish"}}',
            created_at="2026-07-28T12:00:00Z",
        )
        long_play = trader_db.insert_training_example(
            conn, ticker="BVS", features='{"technical": {"direction": "bullish"}}',
            created_at="2026-07-28T12:00:00Z",
        )
        trader_db.label_training_example(conn, trade_close, trade_id="t1", label_win=1, label_return_pct=2.0)
        trader_db.label_training_example(
            conn, long_play, trade_id=None, label_win=0, label_return_pct=-1.0,
            label_horizon="long_play_prediction",
        )
        # Unfiltered -- both, matching pre-long-play behavior for any caller
        # that doesn't pass label_horizon.
        assert len(trader_db.fetch_labeled_training_examples(conn)) == 2

        trade_close_only = trader_db.fetch_labeled_training_examples(conn, label_horizon="trade_close")
        assert len(trade_close_only) == 1
        assert trade_close_only[0]["label_win"] == 1

        long_play_only = trader_db.fetch_labeled_training_examples(conn, label_horizon="long_play_prediction")
        assert len(long_play_only) == 1
        assert long_play_only[0]["label_win"] == 0


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

    # ── Long plays (2026-07-30, params.json risk.long_play) ───────────────

    def test_upsert_defaults_to_standard_play_type(self, conn):
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        row = trader_db.get_position(conn, "AAA")
        assert row["play_type"] == "standard"
        assert row["predicted_by_date"] is None
        assert row["prediction_reason"] is None

    def test_upsert_accepts_long_play_fields(self, conn):
        trader_db.upsert_position(
            conn, ticker="BVS", shares=5.0, entry_price=10.0, entry_time="t1",
            play_type="long", predicted_by_date="2026-08-02", prediction_reason="earnings beat expected",
        )
        row = trader_db.get_position(conn, "BVS")
        assert row["play_type"] == "long"
        assert row["predicted_by_date"] == "2026-08-02"
        assert row["prediction_reason"] == "earnings beat expected"

    def test_scale_in_does_not_change_play_type(self, conn):
        """A scale-in (repeat upsert_position on an existing ticker) must
        never flip a long play back to standard, or vice versa -- play_type
        is set once at initial entry."""
        trader_db.upsert_position(
            conn, ticker="BVS", shares=5.0, entry_price=10.0, entry_time="t1",
            play_type="long", predicted_by_date="2026-08-02", prediction_reason="earnings beat expected",
        )
        # Scale-in call omits play_type -- defaults to "standard" in the
        # function signature, but must NOT overwrite the existing row.
        trader_db.upsert_position(conn, ticker="BVS", shares=8.0, entry_price=10.0, entry_time="t1")
        row = trader_db.get_position(conn, "BVS")
        assert row["play_type"] == "long"
        assert row["predicted_by_date"] == "2026-08-02"
        assert row["shares"] == 8.0

    def test_resolve_long_play_reverts_to_standard(self, conn):
        trader_db.upsert_position(
            conn, ticker="BVS", shares=5.0, entry_price=10.0, entry_time="t1",
            play_type="long", predicted_by_date="2026-08-02", prediction_reason="earnings beat expected",
        )
        trader_db.resolve_long_play(conn, ticker="BVS", updated_at="2026-08-02T16:00:00Z")
        row = trader_db.get_position(conn, "BVS")
        assert row["play_type"] == "standard"
        # predicted_by_date/prediction_reason kept as a permanent audit
        # trail of what was predicted, not cleared.
        assert row["predicted_by_date"] == "2026-08-02"
        assert row["prediction_reason"] == "earnings beat expected"


class TestBacktestExport:
    """2026-08-02: backtest_closed_trades is the export target
    scripts/export_backtest_trades.py writes into the LIVE db -- its own
    table, not `positions` (ticker PK collision) or `training_examples`
    (ml_trainer_service.py doesn't read that table)."""

    def test_get_closed_trades_for_export_excludes_open(self, conn):
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.upsert_position(conn, ticker="BBB", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.close_position(conn, ticker="BBB", closed_at="t2", close_reason="exit", realized_pnl=1.0, realized_return_pct=10.0)
        trades = trader_db.get_closed_trades_for_export(conn)
        assert [t["ticker"] for t in trades] == ["BBB"]

    def test_get_closed_trades_for_export_excludes_fabricated_timestamp_rows(self, conn):
        """entry_time == closed_at marks a pre-migration row with a
        placeholder timestamp -- not a real duration, shouldn't train a
        walk-forward-dated model."""
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.close_position(conn, ticker="AAA", closed_at="t1", close_reason="exit", realized_pnl=1.0, realized_return_pct=10.0)
        assert trader_db.get_closed_trades_for_export(conn) == []

    def test_get_closed_trades_for_export_excludes_null_return_pct(self, conn):
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.execute("UPDATE positions SET status='closed', closed_at='t2' WHERE ticker='AAA'")
        conn.commit()
        assert trader_db.get_closed_trades_for_export(conn) == []

    def test_export_closed_trade_inserts_new_row(self, conn):
        inserted = trader_db.export_closed_trade(
            conn, session_id="s1", ticker="AAA", entry_time="2026-06-01T10:00:00Z",
            closed_at="2026-06-02T15:00:00Z", realized_pnl=5.0, realized_return_pct=10.0,
            sector="Technology", thesis="test", close_reason="target hit",
        )
        assert inserted is True
        row = conn.execute("SELECT * FROM backtest_closed_trades WHERE session_id='s1'").fetchone()
        assert row["ticker"] == "AAA"
        assert row["realized_return_pct"] == 10.0

    def test_export_closed_trade_is_idempotent(self, conn):
        kwargs = dict(
            session_id="s1", ticker="AAA", entry_time="2026-06-01T10:00:00Z",
            closed_at="2026-06-02T15:00:00Z", realized_pnl=5.0, realized_return_pct=10.0,
        )
        first = trader_db.export_closed_trade(conn, **kwargs)
        second = trader_db.export_closed_trade(conn, **kwargs)
        assert first is True
        assert second is False  # already exported, no-op
        count = conn.execute("SELECT COUNT(*) AS n FROM backtest_closed_trades").fetchone()["n"]
        assert count == 1

    def test_export_closed_trade_same_ticker_different_sessions_both_land(self, conn):
        """Two different backtest chains that both happened to trade AAPL
        must NOT collide -- this is the exact scenario `positions`'
        ticker PK would have made impossible."""
        trader_db.export_closed_trade(
            conn, session_id="chain-a", ticker="AAPL", entry_time="t1", closed_at="t2",
            realized_pnl=1.0, realized_return_pct=1.0,
        )
        trader_db.export_closed_trade(
            conn, session_id="chain-b", ticker="AAPL", entry_time="t1", closed_at="t2",
            realized_pnl=2.0, realized_return_pct=2.0,
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM backtest_closed_trades WHERE ticker='AAPL'").fetchone()["n"]
        assert count == 2


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

    def test_get_watchlist_batch_least_recently_evaluated_first(self, conn):
        for t in ["AAA", "BBB", "CCC"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        trader_db.mark_candidates_evaluated(conn, ["AAA"], now="2026-08-01T10:00:00+00:00")
        trader_db.mark_candidates_evaluated(conn, ["BBB"], now="2026-08-01T10:05:00+00:00")
        # CCC has never been evaluated -> front of the queue, then AAA
        # (evaluated longest ago), then BBB.
        assert [c["ticker"] for c in trader_db.get_watchlist_batch(conn, 3)] == ["CCC", "AAA", "BBB"]

    def test_get_watchlist_batch_never_evaluated_sorts_by_added_at(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="OLD", now="2026-08-01T09:00:00+00:00")
        trader_db.upsert_watchlist_candidate(conn, ticker="NEW", now="2026-08-01T10:00:00+00:00")
        assert [c["ticker"] for c in trader_db.get_watchlist_batch(conn, 2)] == ["OLD", "NEW"]

    def test_get_watchlist_batch_respects_limit(self, conn):
        for t in ["AAA", "BBB", "CCC"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        assert len(trader_db.get_watchlist_batch(conn, 1)) == 1
        assert len(trader_db.get_watchlist_batch(conn, 10)) == 3

    def test_batch_then_mark_evaluated_rotates(self, conn):
        """The intended real-usage pattern: batch -> evaluate -> mark --
        confirms a full rotation surfaces every candidate exactly once
        before repeating."""
        for t in ["AAA", "BBB", "CCC", "DDD"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        seen = []
        for i in range(4):
            batch = trader_db.get_watchlist_batch(conn, 1)
            seen.extend(c["ticker"] for c in batch)
            trader_db.mark_candidates_evaluated(
                conn, [c["ticker"] for c in batch], now=f"2026-08-01T10:0{i}:00+00:00")
        assert sorted(seen) == ["AAA", "BBB", "CCC", "DDD"]

    def test_newly_added_candidate_jumps_the_queue(self, conn):
        """The live bug: names added mid-session sat unevaluated for hours
        behind incumbents and were deleted before ever being looked at.
        A brand-new candidate must be seen on the very next batch."""
        for t in ["AAA", "BBB"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        trader_db.mark_candidates_evaluated(conn, ["AAA", "BBB"], now="2026-08-01T10:00:00+00:00")
        trader_db.upsert_watchlist_candidate(conn, ticker="FRESH")
        assert trader_db.get_watchlist_batch(conn, 1)[0]["ticker"] == "FRESH"

    def test_evaluation_does_not_pin_a_candidate_to_the_front(self, conn):
        """The deadlock itself, as a regression test. Under the old scheme
        the batch froze and re-evaluated the same names indefinitely; here
        20 rounds of batch-of-2 over 4 candidates must spread evenly."""
        for t in ["AAA", "BBB", "CCC", "DDD"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        counts = {t: 0 for t in ["AAA", "BBB", "CCC", "DDD"]}
        for i in range(20):
            batch = [c["ticker"] for c in trader_db.get_watchlist_batch(conn, 2)]
            for t in batch:
                counts[t] += 1
            trader_db.mark_candidates_evaluated(conn, batch, now=f"2026-08-01T10:{i:02d}:00+00:00")
        assert set(counts.values()) == {10}

    def test_mark_evaluated_stamps_and_counts(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        trader_db.mark_candidates_evaluated(conn, ["AAA"], now="2026-08-01T10:00:00+00:00")
        trader_db.mark_candidates_evaluated(conn, ["AAA"], now="2026-08-01T10:05:00+00:00")
        rows = {r["ticker"]: r for r in trader_db.get_watchlist_candidates(conn)}
        assert rows["AAA"]["last_evaluated_at"] == "2026-08-01T10:05:00+00:00"
        assert rows["AAA"]["eval_count"] == 2
        assert rows["AAA"]["idle_ticks"] == 0
        assert rows["BBB"]["last_evaluated_at"] is None
        assert rows["BBB"]["eval_count"] == 0
        assert rows["BBB"]["idle_ticks"] == 2  # diagnostic only, nothing decides on it

    def test_touch_does_not_count_as_an_evaluation(self, conn):
        """Being re-discovered by the discovery pool isn't being evaluated
        -- if a touch reset the ordering signal, a frequently-rediscovered
        candidate would hold the front of the queue permanently."""
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.mark_candidates_evaluated(conn, ["AAA"], now="2026-08-01T10:00:00+00:00")
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA", note="rediscovered")
        row = conn.execute("SELECT * FROM watchlist_candidates WHERE ticker = 'AAA'").fetchone()
        assert row["last_evaluated_at"] == "2026-08-01T10:00:00+00:00"
        assert row["eval_count"] == 1

    def test_touch_resets_idle_ticks(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.increment_idle_ticks(conn)
        trader_db.increment_idle_ticks(conn)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA", note="touched again")
        row = conn.execute("SELECT * FROM watchlist_candidates WHERE ticker = 'AAA'").fetchone()
        assert row["idle_ticks"] == 0
        assert row["note"] == "touched again"

    def test_drop_stale_retires_exhausted_candidates(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        for i in range(12):
            trader_db.mark_candidates_evaluated(conn, ["AAA"], now=f"2026-08-01T10:{i:02d}:00+00:00")
        dropped = trader_db.drop_stale_watchlist_candidates(conn, max_evaluations=12)
        assert dropped == ["AAA"]
        assert [r["ticker"] for r in trader_db.get_watchlist_candidates(conn)] == ["BBB"]

    def test_drop_stale_retires_by_added_at_age(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="OLD", now="2026-07-30T10:00:00+00:00")
        trader_db.upsert_watchlist_candidate(conn, ticker="NEW", now="2026-08-01T09:00:00+00:00")
        dropped = trader_db.drop_stale_watchlist_candidates(
            conn, max_age_hours=48, now="2026-08-01T10:00:00+00:00")
        assert dropped == ["OLD"]

    def test_drop_stale_never_retires_on_the_ordering_signal(self, conn):
        """A candidate that has never been evaluated can be arbitrarily
        idle and must still survive -- that's the whole point of the
        split. Under the old scheme this was exactly the row that got
        deleted before ever reaching a batch."""
        trader_db.upsert_watchlist_candidate(conn, ticker="NEGLECTED")
        for _ in range(100):
            trader_db.increment_idle_ticks(conn)
        dropped = trader_db.drop_stale_watchlist_candidates(
            conn, max_evaluations=12, max_age_hours=48)
        assert dropped == []
        assert trader_db.get_watchlist_batch(conn, 1)[0]["ticker"] == "NEGLECTED"

    def test_drop_stale_with_no_thresholds_drops_nothing(self, conn):
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        assert trader_db.drop_stale_watchlist_candidates(conn) == []
        assert len(trader_db.get_watchlist_candidates(conn)) == 1

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

    def test_ceiling_pct_defaults_when_omitted(self, conn):
        """2026-07-28: equity-scaled ceiling addition -- a fresh row with no
        ceiling_pct passed must still satisfy the NOT NULL schema default,
        not error."""
        trader_db.upsert_bankroll_state(conn, ceiling=700.0, growth_rate=0.02, decay_rate=0.01, target_profit_pct=0.01)
        assert trader_db.get_bankroll_state(conn)["ceiling_pct"] == 0.067

    def test_ceiling_pct_explicit_value_stored(self, conn):
        trader_db.upsert_bankroll_state(
            conn, ceiling=700.0, growth_rate=0.02, decay_rate=0.01, target_profit_pct=0.01, ceiling_pct=0.08,
        )
        assert trader_db.get_bankroll_state(conn)["ceiling_pct"] == 0.08

    def test_ceiling_pct_preserved_when_later_write_omits_it(self, conn):
        """The real risk this is guarding against: bankroll.py's existing
        write_bankroll() doesn't know about ceiling_pct and will keep
        calling upsert_bankroll_state() without it on every real trade --
        that must NOT silently reset an already-accumulated ceiling_pct
        back to the schema default."""
        trader_db.upsert_bankroll_state(
            conn, ceiling=700.0, growth_rate=0.02, decay_rate=0.01, target_profit_pct=0.01, ceiling_pct=0.09,
        )
        trader_db.upsert_bankroll_state(conn, ceiling=714.0, growth_rate=0.02, decay_rate=0.01, target_profit_pct=0.01)
        assert trader_db.get_bankroll_state(conn)["ceiling_pct"] == 0.09
        assert trader_db.get_bankroll_state(conn)["ceiling"] == 714.0


class TestBankrollStateMigration:
    def test_ceiling_pct_column_added_to_pre_existing_db(self, tmp_path):
        """Simulates the real live DB: created before ceiling_pct existed
        (no CREATE TABLE run with the new column), then opened by code that
        knows about it -- init_schema()'s migration must add the column
        without dropping existing data."""
        db_path = tmp_path / "pre_migration.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE bankroll_state (
                id INTEGER PRIMARY KEY CHECK (id = 1), ceiling REAL NOT NULL,
                growth_rate REAL NOT NULL, decay_rate REAL NOT NULL, target_profit_pct REAL NOT NULL,
                closed_trades_session INTEGER NOT NULL DEFAULT 0, wins_session INTEGER NOT NULL DEFAULT 0,
                losses_session INTEGER NOT NULL DEFAULT 0, net_pnl_session REAL NOT NULL DEFAULT 0.0,
                total_deployed_session REAL NOT NULL DEFAULT 0.0, lifetime_trades INTEGER NOT NULL DEFAULT 0,
                lifetime_net_pnl REAL NOT NULL DEFAULT 0.0, lifetime_wins INTEGER NOT NULL DEFAULT 0,
                lifetime_losses INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO bankroll_state (id, ceiling, growth_rate, decay_rate, target_profit_pct, updated_at) "
            "VALUES (1, 700.0, 0.02, 0.01, 0.01, 't1')"
        )
        conn.commit()
        conn.close()

        real_conn = trader_db.get_conn(db_path)
        try:
            state = trader_db.get_bankroll_state(real_conn)
            assert state["ceiling"] == 700.0  # pre-existing data preserved
            assert state["ceiling_pct"] == 0.067  # new column, default backfilled
        finally:
            real_conn.close()

    def test_migration_is_idempotent_across_repeated_get_conn_calls(self, tmp_path):
        db_path = tmp_path / "reopened.db"
        conn1 = trader_db.get_conn(db_path)
        conn1.close()
        conn2 = trader_db.get_conn(db_path)  # would raise "duplicate column" if not guarded
        conn2.close()


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


class TestEntryTrainingExampleLookup:
    """2026-08-01: outcome labeling must attach to the BUY row, never to the
    SELL row that happens to be newer (see find_entry_training_example)."""

    def test_prefers_exact_position_link(self, tmp_path):
        conn = trader_db.get_conn(tmp_path / "trader.db")
        older = trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", created_at="t1",
            example_type="entry", position_entry_time="p1")
        trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", created_at="t2",
            example_type="entry", position_entry_time="p2")
        row = trader_db.find_entry_training_example(conn, "AAA", position_entry_time="p1")
        conn.close()
        assert row["id"] == older

    def test_falls_back_to_newest_entry_row(self, tmp_path):
        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", created_at="t1", example_type="entry")
        newest = trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", created_at="t2", example_type="entry")
        row = trader_db.find_entry_training_example(conn, "AAA")
        conn.close()
        assert row["id"] == newest

    def test_never_returns_an_exit_row(self, tmp_path):
        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", created_at="t9", example_type="exit")
        row = trader_db.find_entry_training_example(conn, "AAA")
        conn.close()
        assert row is None

    def test_labeled_entry_rows_are_excluded(self, tmp_path):
        conn = trader_db.get_conn(tmp_path / "trader.db")
        te_id = trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", created_at="t1", example_type="entry")
        trader_db.label_training_example(conn, te_id, trade_id=None, label_win=1, label_return_pct=2.0)
        row = trader_db.find_entry_training_example(conn, "AAA")
        conn.close()
        assert row is None

    def test_legacy_rows_listed_separately(self, tmp_path):
        conn = trader_db.get_conn(tmp_path / "trader.db")
        legacy = trader_db.insert_training_example(conn, ticker="AAA", features="{}", created_at="t1")
        trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", created_at="t2", example_type="entry")
        rows = trader_db.unlabeled_legacy_training_examples(conn, "AAA")
        conn.close()
        assert [r["id"] for r in rows] == [legacy]

    def test_update_features_preserves_existing_links(self, tmp_path):
        conn = trader_db.get_conn(tmp_path / "trader.db")
        te_id = trader_db.insert_training_example(
            conn, ticker="AAA", features="{}", created_at="t1",
            example_type="entry", position_entry_time="p1", decision_id=7)
        trader_db.update_training_example_features(conn, te_id, features='{"technical": {}}')
        row = conn.execute("SELECT * FROM training_examples WHERE id = ?", (te_id,)).fetchone()
        conn.close()
        assert row["features"] == '{"technical": {}}'
        assert row["position_entry_time"] == "p1"
        assert row["decision_id"] == 7
