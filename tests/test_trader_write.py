#!/usr/bin/env python3
"""
Unit tests for scripts/trader_write.py -- the write-capable CLI fronting
trader_db.py (2026-07-28, Phase 6b). Real sqlite3 files under tmp_path via
--db-path, no mocking needed (same convention as trader_query.py's tests).
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import trader_db  # noqa: E402
import trader_write  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "trader.db"


def _run(monkeypatch, capsys, db_path, argv, expect_exit=None):
    monkeypatch.setattr(sys, "argv", ["trader_write.py", "--db-path", str(db_path)] + argv)
    if expect_exit is not None:
        with pytest.raises(SystemExit) as exc_info:
            trader_write.main()
        assert exc_info.value.code == expect_exit
    else:
        trader_write.main()
    return json.loads(capsys.readouterr().out)


class TestWatchlistAdd:
    def test_new_candidate(self, monkeypatch, capsys, db_path):
        result = _run(monkeypatch, capsys, db_path, [
            "watchlist-add", "--ticker", "aaa", "--note", "RSI 58", "--source", "manual",
        ])
        assert result["action"] == "added"
        assert result["candidate"]["ticker"] == "AAA"
        assert result["candidate"]["idle_ticks"] == 0

    def test_touch_resets_idle_ticks(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA", price=10.0, rsi=55.0)
        trader_db.increment_idle_ticks(conn)
        trader_db.increment_idle_ticks(conn)
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-add", "--ticker", "AAA", "--note", "still watching"])

        assert result["action"] == "touched"
        assert result["candidate"]["idle_ticks"] == 0

    def test_touch_without_numeric_flags_preserves_existing_values(self, monkeypatch, capsys, db_path):
        """The correctness note from the plan: a bare touch (no --price/--rsi/
        etc.) must not null out previously-stored signal data."""
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(
            conn, ticker="AAA", price=19.68, rsi=57.7, volume_ratio=1.2, macd_hist=0.4344,
        )
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-add", "--ticker", "AAA", "--note", "touched, no numbers passed"])

        candidate = result["candidate"]
        assert candidate["price"] == 19.68
        assert candidate["rsi"] == 57.7
        assert candidate["volume_ratio"] == 1.2
        assert candidate["macd_hist"] == 0.4344
        assert candidate["note"] == "touched, no numbers passed"

    def test_touch_with_new_numeric_flags_overwrites(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA", price=10.0, rsi=50.0)
        conn.close()

        result = _run(monkeypatch, capsys, db_path, [
            "watchlist-add", "--ticker", "AAA", "--price", "12.5", "--rsi", "60.0",
        ])

        assert result["candidate"]["price"] == 12.5
        assert result["candidate"]["rsi"] == 60.0


class TestWatchlistDropStale:
    def test_default_threshold_from_params(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        for _ in range(24):
            trader_db.increment_idle_ticks(conn)
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-drop-stale"])

        assert result["threshold"] == 24
        assert result["dropped"] == ["AAA"]

    def test_explicit_threshold_overrides_default(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.increment_idle_ticks(conn)
        trader_db.increment_idle_ticks(conn)
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-drop-stale", "--threshold", "2"])

        assert result["dropped"] == ["AAA"]

    def test_nothing_stale_returns_empty(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-drop-stale", "--threshold", "24"])

        assert result["dropped"] == []


class TestWatchlistRemove:
    def test_removes_candidate(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-remove", "--ticker", "aaa"])

        assert result["removed"] == "AAA"
        conn = trader_db.get_conn(db_path)
        try:
            assert trader_db.get_watchlist_candidates(conn) == []
        finally:
            conn.close()


class TestPositionUpdateThesis:
    def test_updates_thesis_preserves_other_fields(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(
            conn, ticker="AAA", shares=3.0, entry_price=30.98, entry_time="2026-07-24T10:05:00Z",
            sector="Technology", thesis="original",
        )
        conn.close()

        result = _run(monkeypatch, capsys, db_path, [
            "position-update-thesis", "--ticker", "aaa", "--thesis", "stop tightened, catalyst next week",
        ])

        assert result["thesis"] == "stop tightened, catalyst next week"
        conn = trader_db.get_conn(db_path)
        try:
            row = trader_db.get_position(conn, "AAA")
            assert row["thesis"] == "stop tightened, catalyst next week"
            assert row["shares"] == 3.0
            assert row["entry_price"] == 30.98
            assert row["sector"] == "Technology"
        finally:
            conn.close()

    def test_unknown_ticker_errors_cleanly(self, monkeypatch, capsys, db_path):
        result = _run(monkeypatch, capsys, db_path, [
            "position-update-thesis", "--ticker", "ZZZ", "--thesis", "x",
        ], expect_exit=1)
        assert "error" in result

    def test_closed_position_errors_cleanly(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.close_position(conn, ticker="AAA", closed_at="t2", close_reason="exit", realized_pnl=1.0, realized_return_pct=1.0)
        conn.close()

        result = _run(monkeypatch, capsys, db_path, [
            "position-update-thesis", "--ticker", "AAA", "--thesis", "x",
        ], expect_exit=1)
        assert "error" in result
