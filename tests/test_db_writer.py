#!/usr/bin/env python3
"""
Unit tests for scripts/db_writer.py -- the in-process write-behind buffer
sitting on top of trader_db.py. Real sqlite3 files under tmp_path, no
mocking needed. Lands unwired (2026-07-28, Phase 1) -- nothing calls this
yet. Each test resets the module-level buffer first since it's process
(not connection) scoped.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import db_writer  # noqa: E402
import trader_db  # noqa: E402


@pytest.fixture(autouse=True)
def clear_buffer():
    db_writer._buffer.clear()
    yield
    db_writer._buffer.clear()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "trader.db"


class TestEnqueue:
    def test_enqueue_known_table_increments_pending_count(self):
        db_writer.enqueue("decisions", {"ticker": "AAA", "timestamp": "t1", "decision": "BUY",
                                         "conviction": 0.5, "rationale": "", "regime": None, "decision_json": None})
        assert db_writer.pending_count() == 1

    def test_enqueue_unknown_table_dropped_not_raised(self):
        db_writer.enqueue("not_a_real_table", {"foo": "bar"})
        assert db_writer.pending_count() == 0


class TestFlushAll:
    def test_flush_writes_buffered_rows_and_clears_buffer(self, db_path):
        db_writer.enqueue("decisions", {"ticker": "AAA", "timestamp": "t1", "decision": "BUY",
                                         "conviction": 0.5, "rationale": "", "regime": None, "decision_json": None})
        db_writer.enqueue("journal", {"timestamp": "t1", "ticker": "AAA", "decision": "BUY",
                                       "rationale": "", "equity": 100.0, "drawdown_pct": 0.0, "decision_id": None})

        ok = db_writer.flush_all(db_path)

        assert ok is True
        assert db_writer.pending_count() == 0
        conn = trader_db.get_conn(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()["n"] == 1
            assert conn.execute("SELECT COUNT(*) AS n FROM journal").fetchone()["n"] == 1
        finally:
            conn.close()

    def test_flush_with_empty_buffer_is_noop_true(self, db_path):
        assert db_writer.flush_all(db_path) is True

    def test_flush_batches_multiple_rows_same_table_in_one_call(self, db_path):
        for i in range(3):
            db_writer.enqueue("decisions", {"ticker": f"T{i}", "timestamp": f"t{i}", "decision": "BUY",
                                             "conviction": 0.5, "rationale": "", "regime": None, "decision_json": None})
        db_writer.flush_all(db_path)
        conn = trader_db.get_conn(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()["n"] == 3
        finally:
            conn.close()

    def test_flush_failure_leaves_buffer_intact_for_retry(self, monkeypatch, db_path):
        db_writer.enqueue("decisions", {"ticker": "AAA", "timestamp": "t1", "decision": "BUY",
                                         "conviction": 0.5, "rationale": "", "regime": None, "decision_json": None})

        def raise_get_conn(path=None):
            raise ConnectionError("simulated open failure")
        monkeypatch.setattr(db_writer.trader_db, "get_conn", raise_get_conn)

        ok = db_writer.flush_all(db_path)

        assert ok is False
        assert db_writer.pending_count() == 1  # not dropped, retryable
