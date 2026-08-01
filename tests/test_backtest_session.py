#!/usr/bin/env python3
"""
Unit tests for scripts/backtest_session.py -- per-session isolated SQLite
files for the historical-replay harness (2026-08-01). Real sqlite3 files
under tmp_path, no mocking. Every test monkeypatches BACKUPS_DIR/BACKTEST_DIR
to tmp_path so nothing touches the real state/backups or state/backtest
directories.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_session as bs  # noqa: E402
import trader_db  # noqa: E402


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    backups_dir = tmp_path / "backups"
    backtest_dir = tmp_path / "backtest"
    backups_dir.mkdir()
    monkeypatch.setattr(bs, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(bs, "BACKTEST_DIR", backtest_dir)
    return backups_dir, backtest_dir


def _make_populated_backup(backups_dir: Path, filename: str = "trader-20260801.db") -> Path:
    """A fake 'live' backup with data in every LIVE_STATE_TABLES table plus
    news_cache, so create_session()'s clearing/preserving behavior is
    actually exercised, not just tested against an empty DB."""
    path = backups_dir / filename
    conn = trader_db.get_conn(path)
    with conn:
        trader_db.upsert_position(conn, ticker="AAPL", shares=1.0, entry_price=200.0, entry_time="t1")
        conn.execute(
            "INSERT INTO decisions (ticker, timestamp, decision) VALUES ('AAPL', 't1', 'BUY')"
        )
        conn.execute(
            "INSERT INTO journal (timestamp, ticker, decision) VALUES ('t1', 'AAPL', 'BUY')"
        )
        conn.execute(
            "INSERT INTO training_examples (ticker, features, created_at) VALUES ('AAPL', '{}', 't1')"
        )
        trader_db.upsert_watchlist_candidate(conn, ticker="MSFT")
        conn.execute(
            "INSERT INTO alpaca_audit_log (timestamp, endpoint, method) VALUES ('t1', '/v2/orders', 'POST')"
        )
        conn.execute(
            "INSERT INTO bankroll_history (timestamp, label, pnl, ceiling_after) VALUES ('t1', 'WIN', 5.0, 700.0)"
        )
        trader_db.upsert_bankroll_state(conn, ceiling=679.0, growth_rate=0.02, decay_rate=0.01, target_profit_pct=0.01)
        conn.execute(
            "INSERT INTO news_cache (url, title, source, published_at, collected_at) "
            "VALUES ('http://x', 'headline', 'rss', 't1', 't1')"
        )
    conn.close()
    return path


class TestCreateSession:
    def test_creates_db_and_manifest(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)

        manifest = bs.create_session("s1", start_date="2026-06-01", end_date="2026-06-10")

        assert manifest["id"] == "s1"
        assert manifest["status"] == "active"
        assert manifest["current_date"] is None
        assert manifest["starting_capital"] == bs.STARTING_CASH_DEFAULT
        assert Path(manifest["db_path"]).exists()
        assert bs._manifest_path("s1").exists()

    def test_seeds_from_latest_backup_by_filename_sort(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir, "trader-20260701.db")
        newest = _make_populated_backup(backups_dir, "trader-20260801.db")

        manifest = bs.create_session("s1", start_date="2026-06-01")

        assert manifest["seeded_from_backup"] == str(newest)

    def test_clears_live_state_tables(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)

        manifest = bs.create_session("s1", start_date="2026-06-01")

        conn = trader_db.get_conn(Path(manifest["db_path"]))
        for table in bs.LIVE_STATE_TABLES:
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            assert n == 0, f"{table} should be cleared, has {n} rows"
        conn.close()

    def test_preserves_news_cache(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)

        manifest = bs.create_session("s1", start_date="2026-06-01")

        conn = trader_db.get_conn(Path(manifest["db_path"]))
        n = conn.execute("SELECT COUNT(*) AS n FROM news_cache").fetchone()["n"]
        conn.close()
        assert n == 1

    def test_resets_bankroll_state_to_starting_capital(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)  # ceiling=679.0 in the "live" copy

        manifest = bs.create_session("s1", start_date="2026-06-01", starting_capital=25_000.0)

        conn = trader_db.get_conn(Path(manifest["db_path"]))
        row = trader_db.get_bankroll_state(conn)
        conn.close()
        assert row["ceiling"] == 25_000.0

    def test_does_not_touch_the_source_backup(self, dirs):
        backups_dir, backtest_dir = dirs
        backup_path = _make_populated_backup(backups_dir)

        bs.create_session("s1", start_date="2026-06-01")

        conn = trader_db.get_conn(backup_path)
        n = conn.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"]
        conn.close()
        assert n == 1, "the source backup file itself must be untouched -- copy, not move/mutate"

    def test_works_with_no_backup_available(self, dirs):
        backups_dir, backtest_dir = dirs  # empty backups_dir, no backup file

        manifest = bs.create_session("s1", start_date="2026-06-01")

        assert manifest["seeded_from_backup"] is None
        assert Path(manifest["db_path"]).exists()

    def test_refuses_to_overwrite_existing_session(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)
        bs.create_session("s1", start_date="2026-06-01")

        with pytest.raises(ValueError, match="already exists"):
            bs.create_session("s1", start_date="2026-06-01")


class TestDayLifecycle:
    def test_start_day_sets_day_in_progress(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)
        bs.create_session("s1", start_date="2026-06-01")

        manifest = bs.start_day("s1", "2026-06-01")

        assert manifest["day_in_progress"] == "2026-06-01"
        assert manifest["current_date"] is None  # not advanced yet -- two-phase

    def test_complete_day_advances_current_date(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)
        bs.create_session("s1", start_date="2026-06-01")
        bs.start_day("s1", "2026-06-01")

        manifest = bs.complete_day("s1", "2026-06-01")

        assert manifest["current_date"] == "2026-06-01"
        assert manifest["day_in_progress"] is None

    def test_complete_day_rejects_mismatched_date(self, dirs):
        """A crashed/timed-out agentTurn must not be able to silently
        advance current_date for a day it didn't actually finish -- this
        is the two-phase-completion safety property from the plan."""
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)
        bs.create_session("s1", start_date="2026-06-01")
        bs.start_day("s1", "2026-06-01")

        with pytest.raises(ValueError, match="doesn't match"):
            bs.complete_day("s1", "2026-06-02")

    def test_complete_day_marks_session_completed_at_end_date(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)
        bs.create_session("s1", start_date="2026-06-01", end_date="2026-06-01")
        bs.start_day("s1", "2026-06-01")

        manifest = bs.complete_day("s1", "2026-06-01")

        assert manifest["status"] == "completed"
        assert manifest["ended_at"] is not None

    def test_start_day_on_nonexistent_session_raises(self, dirs):
        with pytest.raises(FileNotFoundError):
            bs.start_day("ghost", "2026-06-01")

    def test_start_day_on_completed_session_raises(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)
        bs.create_session("s1", start_date="2026-06-01", end_date="2026-06-01")
        bs.start_day("s1", "2026-06-01")
        bs.complete_day("s1", "2026-06-01")

        with pytest.raises(ValueError, match="not active"):
            bs.start_day("s1", "2026-06-02")


class TestResolveSessionDbPath:
    def test_resolves_existing_session(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)
        manifest = bs.create_session("s1", start_date="2026-06-01")

        path = bs.resolve_session_db_path("s1")

        assert path == Path(manifest["db_path"])
        assert path.exists()

    def test_raises_for_nonexistent_session(self, dirs):
        with pytest.raises(FileNotFoundError, match="no backtest session"):
            bs.resolve_session_db_path("ghost")

    def test_raises_if_db_file_missing(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)
        manifest = bs.create_session("s1", start_date="2026-06-01")
        Path(manifest["db_path"]).unlink()

        with pytest.raises(FileNotFoundError, match="DB file is missing"):
            bs.resolve_session_db_path("s1")


class TestAbandonSession:
    def test_marks_abandoned_with_reason(self, dirs):
        backups_dir, backtest_dir = dirs
        _make_populated_backup(backups_dir)
        bs.create_session("s1", start_date="2026-06-01")

        manifest = bs.abandon_session("s1", reason="stale day_in_progress, retried too many times")

        assert manifest["status"] == "abandoned"
        assert "stale day_in_progress" in manifest["notes"]
