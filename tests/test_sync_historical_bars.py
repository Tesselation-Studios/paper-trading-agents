#!/usr/bin/env python3
"""
Unit tests for scripts/sync_historical_bars.py's current_universe()
(2026-07-28, migrated from globbing positions/*.md + regex-parsing
watchlist.md to querying trader_db.py directly). No network -- only
current_universe() is exercised, not main()'s subprocess call into the
backfill script.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import sync_historical_bars  # noqa: E402
import trader_db  # noqa: E402


class TestCurrentUniverse:
    def test_always_includes_spy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        assert sync_historical_bars.current_universe() == ["SPY"]

    def test_unions_open_positions_and_candidates_with_spy(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        conn.close()

        assert sync_historical_bars.current_universe() == ["AAA", "BBB", "SPY"]

    def test_closed_positions_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.close_position(conn, ticker="AAA", closed_at="t2", close_reason="exit", realized_pnl=1.0, realized_return_pct=1.0)
        conn.close()

        assert sync_historical_bars.current_universe() == ["SPY"]

    def test_no_duplicate_if_spy_is_also_a_position(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.upsert_position(conn, ticker="SPY", shares=1.0, entry_price=500.0, entry_time="t1")
        conn.close()

        assert sync_historical_bars.current_universe() == ["SPY"]
