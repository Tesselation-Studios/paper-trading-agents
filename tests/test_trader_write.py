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
    def test_default_thresholds_from_trader_db(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        for i in range(12):
            trader_db.mark_candidates_evaluated(conn, ["AAA"], now=f"2026-08-01T10:{i:02d}:00+00:00")
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-drop-stale"])

        assert result["max_evaluations"] == trader_db.DEFAULT_MAX_EVALUATIONS_BEFORE_DROP
        assert result["max_age_hours"] == trader_db.DEFAULT_MAX_AGE_HOURS_BEFORE_DROP
        assert result["dropped"] == ["AAA"]

    def test_explicit_max_evaluations_overrides_default(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        trader_db.mark_candidates_evaluated(conn, ["AAA"], now="2026-08-01T10:00:00+00:00")
        trader_db.mark_candidates_evaluated(conn, ["AAA"], now="2026-08-01T10:05:00+00:00")
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-drop-stale", "--max-evaluations", "2"])

        assert result["dropped"] == ["AAA"]

    def test_explicit_max_age_hours_overrides_default(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA", now="2026-01-01T00:00:00+00:00")
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-drop-stale", "--max-age-hours", "1"])

        assert result["dropped"] == ["AAA"]

    def test_unevaluated_recent_candidate_is_never_stale(self, monkeypatch, capsys, db_path):
        """The live deadlock's victim: a candidate sitting idle without
        having been evaluated must survive drop-stale, however long it has
        been waiting."""
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        for _ in range(50):
            trader_db.increment_idle_ticks(conn)
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-drop-stale"])

        assert result["dropped"] == []

    def test_explicit_interest_min_score_drops_low_scorers(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="BORING")
        for i in range(3):
            trader_db.mark_candidates_evaluated(conn, ["BORING"], now=f"2026-08-13T10:0{i}:00+00:00")
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-drop-stale", "--interest-min-score", "2"])

        assert result["interest_min_score"] == 2
        assert result["dropped"] == ["BORING"]


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


class TestWatchlistMarkEvaluated:
    def test_stamps_evaluated_and_bumps_everyone_else(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        for t in ["AAA", "BBB", "CCC"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        conn.close()

        result = _run(monkeypatch, capsys, db_path, ["watchlist-mark-evaluated", "--tickers", "aaa,bbb"])

        assert result["evaluated_this_tick"] == ["AAA", "BBB"]
        conn = trader_db.get_conn(db_path)
        try:
            rows = {r["ticker"]: r for r in trader_db.get_watchlist_candidates(conn)}
        finally:
            conn.close()
        assert rows["AAA"]["eval_count"] == 1
        assert rows["AAA"]["last_evaluated_at"] is not None
        assert rows["BBB"]["eval_count"] == 1
        assert rows["CCC"]["eval_count"] == 0
        assert rows["CCC"]["last_evaluated_at"] is None
        assert {t: r["idle_ticks"] for t, r in rows.items()} == {"AAA": 0, "BBB": 0, "CCC": 1}

    def test_entry_signal_tickers_scores_interest(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        for t in ["AAA", "BBB"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        conn.close()

        result = _run(monkeypatch, capsys, db_path, [
            "watchlist-mark-evaluated", "--tickers", "AAA,BBB", "--entry-signal-tickers", "AAA",
        ])

        assert result["entry_signal_tickers"] == ["AAA"]
        conn = trader_db.get_conn(db_path)
        try:
            rows = {r["ticker"]: r for r in trader_db.get_watchlist_candidates(conn)}
        finally:
            conn.close()
        assert rows["AAA"]["interest_score"] == 2
        assert rows["BBB"]["interest_score"] == 0

    def test_repeated_calls_produce_rotation(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        for t in ["AAA", "BBB"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        conn.close()

        _run(monkeypatch, capsys, db_path, ["watchlist-mark-evaluated", "--tickers", "AAA"])
        _run(monkeypatch, capsys, db_path, ["watchlist-mark-evaluated", "--tickers", "AAA"])

        conn = trader_db.get_conn(db_path)
        try:
            batch = trader_db.get_watchlist_batch(conn, 1)
        finally:
            conn.close()
        assert batch[0]["ticker"] == "BBB"

    def test_tree_match_sets_state_per_ticker(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        for t in ["AAA", "BBB"]:
            trader_db.upsert_watchlist_candidate(conn, ticker=t)
        conn.close()

        result = _run(monkeypatch, capsys, db_path, [
            "watchlist-mark-evaluated", "--tickers", "AAA,BBB",
            "--tree-match", "AAA:active,BBB:watch",
        ])

        assert result["tree_match_state"] == {"AAA": "active", "BBB": "watch"}
        conn = trader_db.get_conn(db_path)
        try:
            rows = {r["ticker"]: r for r in trader_db.get_watchlist_candidates(conn)}
        finally:
            conn.close()
        assert rows["AAA"]["tree_match_state"] == "active"
        assert rows["BBB"]["tree_match_state"] == "watch"


class TestWatchlistRecordResearch:
    def test_stamps_confidence_and_note(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA")
        conn.close()

        result = _run(monkeypatch, capsys, db_path, [
            "watchlist-record-research", "--ticker", "aaa", "--confidence", "0.8",
            "--note", "recent contract win, no red flags",
        ])

        assert result["ticker"] == "AAA"
        assert result["candidate"]["research_confidence"] == 0.8
        assert result["candidate"]["research_note"] == "recent contract win, no red flags"
        assert result["candidate"]["last_researched_at"] is not None


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

    def test_verdict_appends_recheck_log_row_without_touching_thesis_blob(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1",
                                   thesis="original blob")
        conn.close()

        result = _run(monkeypatch, capsys, db_path, [
            "position-update-thesis", "--ticker", "AAA", "--verdict", "weakening", "--note", "RSI rolled over",
        ])
        assert result["verdict"] == "weakening"
        assert result["note"] == "RSI rolled over"
        assert "thesis" not in result

        conn = trader_db.get_conn(db_path)
        try:
            row = trader_db.get_position(conn, "AAA")
            assert row["thesis"] == "original blob"  # untouched
            assert row["thesis_check_count"] == 1
            assert row["thesis_last_checked_at"] is not None
            log = trader_db.get_thesis_log(conn, "AAA")
            assert log[0]["event_type"] == "recheck"
            assert log[0]["verdict"] == "weakening"
            assert log[0]["note"] == "RSI rolled over"
        finally:
            conn.close()

    def test_thesis_and_verdict_together(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.close()

        result = _run(monkeypatch, capsys, db_path, [
            "position-update-thesis", "--ticker", "AAA",
            "--thesis", "updated read", "--verdict", "intact",
        ])
        assert result["thesis"] == "updated read"
        assert result["verdict"] == "intact"

    def test_neither_thesis_nor_verdict_errors_cleanly(self, monkeypatch, capsys, db_path):
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.close()

        result = _run(monkeypatch, capsys, db_path, [
            "position-update-thesis", "--ticker", "AAA",
        ], expect_exit=1)
        assert "error" in result
