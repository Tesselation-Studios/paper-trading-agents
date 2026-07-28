#!/usr/bin/env python3
"""Fail-open tests for scripts/decisions.py (2026-07-28) -- confirm a
Postgres outage degrades gracefully (returns an error dict) instead of
raising out of tick_prompt.md step 9's live decision-logging call."""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import decisions  # noqa: E402


def _raise_get_conn(*args, **kwargs):
    raise ConnectionError("simulated DB outage")


class _FailingCursorConn:
    def cursor(self):
        raise RuntimeError("simulated write failure")

    def close(self):
        pass


class _WorkingConn:
    """Returns decision_id=42 on the first execute/fetchone, no-ops otherwise."""

    class _Cursor:
        def execute(self, *a, **kw):
            pass

        def fetchone(self):
            return (42,)

    def cursor(self):
        return self._Cursor()

    def commit(self):
        pass

    def close(self):
        pass


class TestRecordDecisionFailOpen:
    def test_get_conn_failure_returns_error_dict(self, monkeypatch):
        monkeypatch.setattr(decisions.db, "get_conn", _raise_get_conn)
        result = decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY")
        assert "error" in result
        assert result["decision_id"] is None
        assert result["training_example_id"] is None

    def test_write_failure_returns_error_dict(self, monkeypatch):
        monkeypatch.setattr(decisions.db, "get_conn", lambda: _FailingCursorConn())
        result = decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY")
        assert "error" in result
        assert result["decision_id"] is None

    def test_training_example_failure_still_returns_decision_id(self, monkeypatch):
        monkeypatch.setattr(decisions.db, "get_conn", lambda: _WorkingConn())

        def raise_insert(**kw):
            raise RuntimeError("simulated training_examples failure")
        monkeypatch.setattr(decisions.db, "insert_training_example", raise_insert)

        result = decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY")
        assert result["decision_id"] == 42
        assert result["training_example_id"] is None
        assert "error" not in result


class TestRecordJournalFailOpen:
    def test_get_conn_failure_returns_error_dict(self, monkeypatch):
        monkeypatch.setattr(decisions.db, "get_conn", _raise_get_conn)
        result = decisions.record_journal(trader_id="stonks", ticker="AAA", decision_text="HOLD")
        assert "error" in result
        assert result["journal_id"] is None

    def test_write_failure_returns_error_dict(self, monkeypatch):
        monkeypatch.setattr(decisions.db, "get_conn", lambda: _FailingCursorConn())
        result = decisions.record_journal(trader_id="stonks", ticker="AAA", decision_text="HOLD")
        assert "error" in result
        assert result["journal_id"] is None


class TestRecordTradeCloseFailOpen:
    def test_get_conn_failure_returns_error_dict(self, monkeypatch):
        monkeypatch.setattr(decisions.db, "get_conn", _raise_get_conn)
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=1.0, return_pct=1.0,
        )
        assert "error" in result
        assert result["labeled"] is False

    def test_query_failure_returns_error_dict(self, monkeypatch):
        monkeypatch.setattr(decisions.db, "get_conn", lambda: _FailingCursorConn())
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=1.0, return_pct=1.0,
        )
        assert "error" in result
        assert result["labeled"] is False
