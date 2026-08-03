#!/usr/bin/env python3
"""
Unit tests for scripts/trader_query.py -- the CLI query tool fronting
trader_db.py. Real sqlite3 files under tmp_path via --db-path, no mocking
needed (same convention as promote_candidates.py's own tests).
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import trader_db  # noqa: E402
import trader_query  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "trader.db"


def _run(monkeypatch, capsys, db_path, argv):
    monkeypatch.setattr(sys, "argv", ["trader_query.py", "--db-path", str(db_path)] + argv)
    trader_query.main()
    return json.loads(capsys.readouterr().out)


class TestPositions:
    def test_no_ticker_lists_open_positions(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["positions"])
        assert len(result) == 1
        assert result[0]["ticker"] == "AAA"

    def test_ticker_filter_returns_single_position(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["positions", "--ticker", "aaa"])
        assert result["ticker"] == "AAA"

    def test_unknown_ticker_returns_error(self, monkeypatch, capsys, db_path):
        result = _run(monkeypatch, capsys, db_path, ["positions", "--ticker", "ZZZ"])
        assert "error" in result

    def test_with_history_includes_thesis_log(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1",
                                   thesis_claim="breakout thesis")
        trader_db.log_thesis_event(conn, ticker="AAA", event_type="entry", claim="breakout thesis")
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["positions", "--ticker", "aaa", "--with-history"])
        assert result["ticker"] == "AAA"
        assert len(result["thesis_log"]) == 1
        assert result["thesis_log"][0]["claim"] == "breakout thesis"

    def test_without_with_history_flag_omits_thesis_log(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["positions", "--ticker", "aaa"])
        assert "thesis_log" not in result

    def test_with_history_on_unknown_ticker_still_errors_cleanly(self, monkeypatch, capsys, db_path):
        result = _run(monkeypatch, capsys, db_path, ["positions", "--ticker", "ZZZ", "--with-history"])
        assert "error" in result


class TestWatchlist:
    def test_lists_candidates(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="LDRX", source="discovery_pool gen 2")
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["watchlist"])
        assert result[0]["ticker"] == "LDRX"

    def test_no_batch_flag_returns_full_list(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        for t in ["AAA", "BBB", "CCC"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["watchlist"])
        assert len(result) == 3

    def test_batch_flag_bounds_result_count(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        for t in ["AAA", "BBB", "CCC"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["watchlist", "--batch", "2"])
        assert len(result) == 2

    def test_batch_flag_returns_least_recently_evaluated_first(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        trader_db.mark_candidates_evaluated(conn, ["AAA"], now="2026-08-01T10:00:00+00:00")
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["watchlist", "--batch", "1"])
        assert result[0]["ticker"] == "BBB"  # never evaluated -> front of queue


class TestBankroll:
    def test_state_returns_current(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_bankroll_state(conn, ceiling=700.0, growth_rate=0.02, decay_rate=0.01, target_profit_pct=0.01)
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["bankroll"])
        assert result["ceiling"] == 700.0

    def test_missing_state_returns_error(self, monkeypatch, capsys, db_path):
        result = _run(monkeypatch, capsys, db_path, ["bankroll"])
        assert "error" in result

    def test_history_flag_returns_history_rows(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.record_bankroll_history(conn, timestamp="t1", label="WIN", pnl=1.0, ceiling_after=350.0)
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["bankroll", "--history"])
        assert result[0]["label"] == "WIN"


class TestDecisions:
    def test_filters_by_ticker(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.insert_decision(conn, ticker="AAA", timestamp="t1", decision="BUY")
        trader_db.insert_decision(conn, ticker="BBB", timestamp="t1", decision="BUY")
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["decisions", "--ticker", "aaa"])
        assert len(result) == 1
        assert result[0]["ticker"] == "AAA"

    def test_no_ticker_returns_all_within_limit(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.insert_decision(conn, ticker="AAA", timestamp="t1", decision="BUY")
        trader_db.insert_decision(conn, ticker="BBB", timestamp="t2", decision="SELL")
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["decisions"])
        assert len(result) == 2


class TestTrainingExamples:
    def test_labeled_only_excludes_unlabeled(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        te_id = trader_db.insert_training_example(conn, ticker="AAA", features="{}", created_at="t1")
        trader_db.insert_training_example(conn, ticker="BBB", features="{}", created_at="t1")
        trader_db.label_training_example(conn, te_id, trade_id="x", label_win=1, label_return_pct=1.0)
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["training-examples", "--labeled-only"])
        assert len(result) == 1


class TestNews:
    def test_missing_ticker_returns_error(self, monkeypatch, capsys, db_path):
        result = _run(monkeypatch, capsys, db_path, ["news"])
        assert "error" in result

    def test_returns_matching_articles(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_news_articles(conn, [
            {"url": "http://a", "title": "AAA news", "source": "rss", "published_at": "2026-07-28T11:00:00Z",
             "collected_at": "2026-07-28T11:00:00Z", "tickers": '["AAA"]', "sentiment_score": 0.5},
        ])
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["news", "--ticker", "AAA", "--hours", "24"])
        assert len(result) == 1


class TestAuditLog:
    def test_returns_recent_rows(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.insert_alpaca_audit_row(conn, timestamp="t1", endpoint="orders", method="GET")
        conn.close()
        result = _run(monkeypatch, capsys, db_path, ["audit-log"])
        assert len(result) == 1
        assert result[0]["endpoint"] == "orders"
