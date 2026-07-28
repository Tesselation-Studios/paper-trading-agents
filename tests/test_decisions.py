#!/usr/bin/env python3
"""Tests for scripts/decisions.py (migrated 2026-07-28 from remote Postgres
to local trader_db.py). Real sqlite3 files under tmp_path via db_path=,
matching trader_db.py's own test convention -- plus fail-open tests for a
DB-unavailable scenario (mocked), since that risk carries over from the
Postgres days (tick_prompt.md step 9 calls this on every BUY/SELL)."""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import decisions  # noqa: E402
import trader_db  # noqa: E402


def _raise_get_conn(*args, **kwargs):
    raise ConnectionError("simulated DB outage")


class TestRecordDecision:
    def test_writes_decision_and_training_example(self, tmp_path):
        db_path = tmp_path / "trader.db"
        result = decisions.record_decision(
            trader_id="stonks", ticker="AAA", action="BUY", rationale="momentum entry",
            conviction=0.6, regime="momentum_bull", features={"technical": {"direction": "bullish"}},
            db_path=db_path,
        )
        assert result["decision_id"] is not None
        assert result["training_example_id"] is not None

        conn = trader_db.get_conn(db_path)
        try:
            row = conn.execute("SELECT * FROM decisions WHERE id = ?", (result["decision_id"],)).fetchone()
            assert row["ticker"] == "AAA"
            assert row["conviction"] == 0.6
        finally:
            conn.close()

    def test_get_conn_failure_returns_error_dict(self, monkeypatch, tmp_path):
        monkeypatch.setattr(decisions.trader_db, "get_conn", _raise_get_conn)
        result = decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY", db_path=tmp_path / "x.db")
        assert "error" in result
        assert result["decision_id"] is None
        assert result["training_example_id"] is None

    def test_training_example_failure_still_returns_decision_id(self, monkeypatch, tmp_path):
        real_insert = trader_db.insert_training_example

        def raise_insert(*a, **kw):
            raise RuntimeError("simulated training_examples failure")
        monkeypatch.setattr(decisions.trader_db, "insert_training_example", raise_insert)

        result = decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY", db_path=tmp_path / "trader.db")
        assert result["decision_id"] is not None
        assert result["training_example_id"] is None
        assert "error" not in result
        monkeypatch.setattr(decisions.trader_db, "insert_training_example", real_insert)


class TestRecordJournal:
    def test_writes_journal_entry(self, tmp_path):
        db_path = tmp_path / "trader.db"
        result = decisions.record_journal(
            trader_id="stonks", ticker="AAA", decision_text="HOLD", rationale="no change",
            equity=10415.0, drawdown_pct=0.0, db_path=db_path,
        )
        assert result["journal_id"] is not None

    def test_get_conn_failure_returns_error_dict(self, monkeypatch, tmp_path):
        monkeypatch.setattr(decisions.trader_db, "get_conn", _raise_get_conn)
        result = decisions.record_journal(trader_id="stonks", ticker="AAA", decision_text="HOLD", db_path=tmp_path / "x.db")
        assert "error" in result
        assert result["journal_id"] is None


class TestRecordTradeClose:
    def test_labels_most_recent_unlabeled_example(self, tmp_path):
        db_path = tmp_path / "trader.db"
        decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY", db_path=db_path)

        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=12.5, return_pct=4.2, db_path=db_path,
        )
        assert result["labeled"] is True

        conn = trader_db.get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM training_examples WHERE id = ?", (result["training_example_id"],)
            ).fetchone()
            assert row["label_win"] == 1
            assert row["label_return_pct"] == 4.2
        finally:
            conn.close()

    def test_loss_labels_win_zero(self, tmp_path):
        db_path = tmp_path / "trader.db"
        decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY", db_path=db_path)
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=-3.0, return_pct=-2.5, db_path=db_path,
        )
        conn = trader_db.get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM training_examples WHERE id = ?", (result["training_example_id"],)
            ).fetchone()
            assert row["label_win"] == 0
        finally:
            conn.close()

    def test_no_unlabeled_example_returns_error(self, tmp_path):
        db_path = tmp_path / "trader.db"
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="ZZZ", trade_id=None, pnl=1.0, return_pct=1.0, db_path=db_path,
        )
        assert "error" in result
        assert result["labeled"] is False

    def test_get_conn_failure_returns_error_dict(self, monkeypatch, tmp_path):
        monkeypatch.setattr(decisions.trader_db, "get_conn", _raise_get_conn)
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=1.0, return_pct=1.0, db_path=tmp_path / "x.db",
        )
        assert "error" in result
        assert result["labeled"] is False
