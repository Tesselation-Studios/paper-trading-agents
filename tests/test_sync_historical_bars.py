#!/usr/bin/env python3
"""
Unit tests for scripts/sync_historical_bars.py's current_universe() and
sync_universe() (2026-07-28: migrated from globbing positions/*.md +
regex-parsing watchlist.md to querying trader_db.py directly. 2026-08-01:
current_universe() now includes closed positions too, and sync_universe()
persists an "ever synced" superset in bars_universe_history.json so a
ticker's bar history keeps accumulating after it stops being open/
watchlisted -- otherwise it silently goes stale and becomes unreplayable
for historical backtesting). No network -- only these two functions are
exercised, not main()'s subprocess call into the backfill script.
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

    def test_closed_positions_included(self, tmp_path, monkeypatch):
        """2026-08-01: reversed from the old exclude-closed behavior -- a
        ticker Stan sold shouldn't silently stop having its bar history
        synced, or it becomes unreplayable later. positions is keyed by
        ticker, so this table stays small regardless of how many trades
        have happened -- no unbounded growth risk from including closed."""
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.close_position(conn, ticker="AAA", closed_at="t2", close_reason="exit", realized_pnl=1.0, realized_return_pct=1.0)
        conn.close()

        assert sync_historical_bars.current_universe() == ["AAA", "SPY"]

    def test_no_duplicate_if_spy_is_also_a_position(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.upsert_position(conn, ticker="SPY", shares=1.0, entry_price=500.0, entry_time="t1")
        conn.close()

        assert sync_historical_bars.current_universe() == ["SPY"]


class TestSyncUniverse:
    def test_persists_current_universe_to_history_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        history_file = tmp_path / "bars_universe_history.json"
        monkeypatch.setattr(sync_historical_bars, "UNIVERSE_HISTORY_FILE", history_file)
        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        conn.close()

        result = sync_historical_bars.sync_universe()

        assert result == ["BBB", "SPY"]
        assert history_file.exists()
        import json
        assert json.loads(history_file.read_text()) == ["BBB", "SPY"]

    def test_a_ticker_dropped_from_current_universe_stays_in_the_synced_set(self, tmp_path, monkeypatch):
        """The core fix: BBB was watchlisted (synced once), then dropped
        entirely (no longer open, no longer watchlisted) -- it must still
        appear in the next sync_universe() result, not silently vanish."""
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        history_file = tmp_path / "bars_universe_history.json"
        monkeypatch.setattr(sync_historical_bars, "UNIVERSE_HISTORY_FILE", history_file)

        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        conn.close()
        first = sync_historical_bars.sync_universe()
        assert "BBB" in first

        # BBB rotates off the watchlist entirely -- delete the candidate row
        # the same way drop-stale does, simulating current_universe() no
        # longer seeing it at all.
        conn = trader_db.get_conn(tmp_path / "trader.db")
        conn.execute("DELETE FROM watchlist_candidates WHERE ticker = 'BBB'")
        conn.commit()
        conn.close()
        assert "BBB" not in sync_historical_bars.current_universe()

        second = sync_historical_bars.sync_universe()
        assert "BBB" in second

    def test_history_file_grows_monotonically_across_multiple_syncs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        history_file = tmp_path / "bars_universe_history.json"
        monkeypatch.setattr(sync_historical_bars, "UNIVERSE_HISTORY_FILE", history_file)

        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        conn.close()
        sync_historical_bars.sync_universe()

        conn = trader_db.get_conn(tmp_path / "trader.db")
        conn.execute("DELETE FROM watchlist_candidates WHERE ticker = 'BBB'")
        trader_db.upsert_watchlist_candidate(conn, ticker="CCC")
        conn.commit()
        conn.close()
        result = sync_historical_bars.sync_universe()

        assert result == ["BBB", "CCC", "SPY"]
