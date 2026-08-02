#!/usr/bin/env python3
"""
Unit tests for scripts/export_backtest_trades.py -- copies a backtest
session's closed trades into the live DB's backtest_closed_trades table
(2026-08-02). Real sqlite3 files under tmp_path; monkeypatches
backtest_session.BACKUPS_DIR/BACKTEST_DIR and trader_db.DB_PATH so nothing
touches the real state/backtest or state/trader.db.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_session as bs  # noqa: E402
import trader_db  # noqa: E402
import export_backtest_trades as ebt  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    backups_dir = tmp_path / "backups"
    backtest_dir = tmp_path / "backtest"
    backups_dir.mkdir()
    live_db_path = tmp_path / "trader.db"

    monkeypatch.setattr(bs, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(bs, "BACKTEST_DIR", backtest_dir)
    monkeypatch.setattr(trader_db, "DB_PATH", live_db_path)
    return {"live_db_path": live_db_path}


def _open_and_close(session_id: str, ticker: str, entry_time: str, closed_at: str,
                     realized_pnl: float, realized_return_pct: float) -> None:
    db_path = bs.resolve_session_db_path(session_id)
    conn = trader_db.get_conn(db_path)
    try:
        with conn:
            trader_db.upsert_position(conn, ticker=ticker, shares=1.0, entry_price=10.0, entry_time=entry_time)
        trader_db.close_position(
            conn, ticker=ticker, closed_at=closed_at, close_reason="test exit",
            realized_pnl=realized_pnl, realized_return_pct=realized_return_pct,
        )
    finally:
        conn.close()


class TestExportSession:
    def test_missing_session_raises(self, env):
        with pytest.raises(FileNotFoundError):
            ebt.export_session("no-such-session")

    def test_exports_closed_trades_only(self, env):
        bs.create_session("s1", start_date="2026-06-01")
        _open_and_close("s1", "AAA", "2026-06-01T10:00:00Z", "2026-06-01T15:00:00Z", 1.0, 10.0)
        # BBB stays open -- should not be exported
        db_path = bs.resolve_session_db_path("s1")
        conn = trader_db.get_conn(db_path)
        with conn:
            trader_db.upsert_position(conn, ticker="BBB", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.close()

        result = ebt.export_session("s1")
        assert result["session_id"] == "s1"
        assert result["total_closed_in_session"] == 1
        assert result["exported"] == 1
        assert result["skipped_duplicate"] == 0

        live_conn = trader_db.get_conn()
        rows = live_conn.execute("SELECT * FROM backtest_closed_trades").fetchall()
        live_conn.close()
        assert len(rows) == 1
        assert rows[0]["ticker"] == "AAA"
        assert rows[0]["session_id"] == "s1"

    def test_live_positions_table_untouched(self, env):
        """The whole point of a separate export table -- a live position on
        the same ticker (open or closed) must survive an export byte-for-byte."""
        live_conn = trader_db.get_conn()
        with live_conn:
            trader_db.upsert_position(live_conn, ticker="AAA", shares=99.0, entry_price=500.0, entry_time="live-t1")
        live_conn.close()

        bs.create_session("s1", start_date="2026-06-01")
        _open_and_close("s1", "AAA", "2026-06-01T10:00:00Z", "2026-06-01T15:00:00Z", 1.0, 10.0)
        ebt.export_session("s1")

        live_conn = trader_db.get_conn()
        live_position = trader_db.get_position(live_conn, "AAA")
        live_conn.close()
        assert live_position["shares"] == 99.0
        assert live_position["status"] == "open"

    def test_rerun_is_idempotent(self, env):
        bs.create_session("s1", start_date="2026-06-01")
        _open_and_close("s1", "AAA", "2026-06-01T10:00:00Z", "2026-06-01T15:00:00Z", 1.0, 10.0)

        first = ebt.export_session("s1")
        second = ebt.export_session("s1")
        assert first["exported"] == 1
        assert second["exported"] == 0
        assert second["skipped_duplicate"] == 1

        live_conn = trader_db.get_conn()
        count = live_conn.execute("SELECT COUNT(*) AS n FROM backtest_closed_trades").fetchone()["n"]
        live_conn.close()
        assert count == 1

    def test_two_sessions_export_independently(self, env):
        bs.create_session("chain-a", start_date="2026-06-01")
        bs.create_session("chain-b", start_date="2026-06-01")
        _open_and_close("chain-a", "AAA", "2026-06-01T10:00:00Z", "2026-06-01T15:00:00Z", 1.0, 5.0)
        _open_and_close("chain-b", "AAA", "2026-06-01T10:00:00Z", "2026-06-01T15:00:00Z", 2.0, 8.0)

        ebt.export_session("chain-a")
        ebt.export_session("chain-b")

        live_conn = trader_db.get_conn()
        rows = live_conn.execute("SELECT session_id, realized_return_pct FROM backtest_closed_trades WHERE ticker='AAA' ORDER BY session_id").fetchall()
        live_conn.close()
        assert [(r["session_id"], r["realized_return_pct"]) for r in rows] == [("chain-a", 5.0), ("chain-b", 8.0)]
