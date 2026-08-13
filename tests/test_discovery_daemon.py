#!/usr/bin/env python3
"""
Unit tests for scripts/discovery_daemon.py -- the pure cursor/chunk math,
DB-facing cycle logic, and health check. No network -- screen_tickers/
fetch_broad_universe/confirm_with_news are all monkeypatched, matching
every other script's no-network test convention in this repo. The
daemon's outer while-True loop itself is NOT tested, matching this repo's
precedent (discovery_scan.main()/discovery_urgency_check.main() don't
test their own entrypoint loops either) -- only the functions it calls.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import discovery_daemon  # noqa: E402
import discovery_db  # noqa: E402
import trader_db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = discovery_db.get_conn(tmp_path / "pool.db")
    yield c
    c.close()


class TestAdvanceCursor:
    def test_normal_advance(self):
        pos, wrapped = discovery_daemon.advance_cursor(0, 10, 100)
        assert pos == 10
        assert wrapped is False

    def test_wraps_at_end(self):
        pos, wrapped = discovery_daemon.advance_cursor(95, 10, 100)
        assert pos == 5
        assert wrapped is True

    def test_exact_boundary_wraps(self):
        pos, wrapped = discovery_daemon.advance_cursor(90, 10, 100)
        assert pos == 0
        assert wrapped is True

    def test_chunk_size_ge_universe_size(self):
        pos, wrapped = discovery_daemon.advance_cursor(0, 200, 50)
        assert pos == 0
        assert wrapped is True

    def test_empty_universe(self):
        pos, wrapped = discovery_daemon.advance_cursor(0, 10, 0)
        assert pos == 0
        assert wrapped is False


class TestGetChunk:
    def test_normal_slice(self):
        universe = list("ABCDE")
        assert discovery_daemon.get_chunk(universe, 1, 2) == ["B", "C"]

    def test_wraps_mid_chunk(self):
        universe = list("ABCDE")
        assert discovery_daemon.get_chunk(universe, 3, 4) == ["D", "E", "A", "B"]

    def test_oversized_chunk_returns_whole_universe(self):
        universe = list("ABC")
        assert discovery_daemon.get_chunk(universe, 0, 10) == ["A", "B", "C"]

    def test_empty_universe(self):
        assert discovery_daemon.get_chunk([], 0, 10) == []


class TestRefreshUniverseIfStale:
    def test_noop_when_fresh(self, conn, monkeypatch):
        calls = []
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe",
                             lambda **k: calls.append(1) or ["A", "B"])
        discovery_db.upsert_universe_snapshot(conn, ["X", "Y"], generation=1, fetched_at="2026-07-27T12:00:00+00:00")
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["universe_last_refreshed_at"] = "2026-07-27T12:00:00+00:00"
        cursor_state["universe_generation"] = 1
        universe, new_state, refreshed = discovery_daemon.refresh_universe_if_stale(
            conn, cursor_state, refresh_interval_seconds=86400, now="2026-07-27T12:05:00+00:00",
        )
        assert refreshed is False
        assert universe == ["X", "Y"]
        assert calls == []

    def test_refetches_when_stale(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe",
                             lambda **k: ["NEW1", "NEW2"])
        discovery_db.upsert_universe_snapshot(conn, ["OLD"], generation=1, fetched_at="2026-07-27T00:00:00+00:00")
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["universe_last_refreshed_at"] = "2026-07-27T00:00:00+00:00"
        cursor_state["universe_generation"] = 1
        universe, new_state, refreshed = discovery_daemon.refresh_universe_if_stale(
            conn, cursor_state, refresh_interval_seconds=3600, now="2026-07-27T12:00:00+00:00",
        )
        assert refreshed is True
        assert universe == ["NEW1", "NEW2"]
        assert new_state["universe_generation"] == 2
        assert discovery_db.get_universe_snapshot(conn) == ["NEW1", "NEW2"]

    def test_force_refetches_even_if_fresh(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe", lambda **k: ["A"])
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["universe_last_refreshed_at"] = "2026-07-27T12:00:00+00:00"
        cursor_state["universe_generation"] = 1
        universe, new_state, refreshed = discovery_daemon.refresh_universe_if_stale(
            conn, cursor_state, refresh_interval_seconds=86400, now="2026-07-27T12:00:01+00:00", force=True,
        )
        assert refreshed is True

    def test_refetches_when_cursor_fresh_but_db_snapshot_missing(self, conn, monkeypatch):
        """Regression: cursor_state (state/discovery_daemon.json) is not
        tied to any one db file. Confirmed live 2026-07-27 -- a --db-path
        override run warmed the cursor file's universe_last_refreshed_at,
        then a run against the real (different) db silently got zero
        candidates because it trusted the fresh-looking timestamp instead
        of checking whether THIS db actually has that snapshot."""
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe",
                             lambda **k: ["REAL1", "REAL2"])
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["universe_last_refreshed_at"] = "2026-07-27T12:00:00+00:00"
        cursor_state["universe_generation"] = 1
        # conn has NO universe_snapshot rows at all -- simulates a fresh/
        # different db file despite the cursor claiming generation 1 is current.
        universe, new_state, refreshed = discovery_daemon.refresh_universe_if_stale(
            conn, cursor_state, refresh_interval_seconds=86400, now="2026-07-27T12:05:00+00:00",
        )
        assert refreshed is True
        assert universe == ["REAL1", "REAL2"]
        assert new_state["universe_generation"] == 2

    def test_refetches_when_db_generation_mismatches_cursor(self, conn, monkeypatch):
        """Same class of bug, different trigger: the db has SOME snapshot,
        but from a different generation than the cursor claims is current."""
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe",
                             lambda **k: ["NEW"])
        discovery_db.upsert_universe_snapshot(conn, ["OLD_GEN"], generation=1, fetched_at="2026-07-27T11:00:00+00:00")
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["universe_last_refreshed_at"] = "2026-07-27T12:00:00+00:00"
        cursor_state["universe_generation"] = 2  # cursor thinks gen 2, db only has gen 1
        universe, new_state, refreshed = discovery_daemon.refresh_universe_if_stale(
            conn, cursor_state, refresh_interval_seconds=86400, now="2026-07-27T12:05:00+00:00",
        )
        assert refreshed is True
        assert universe == ["NEW"]

    def test_generation_increments_only_on_real_refresh(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe", lambda **k: ["A"])
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        discovery_db.upsert_universe_snapshot(conn, ["A"], generation=1, fetched_at="2026-07-27T12:00:00+00:00")
        cursor_state["universe_last_refreshed_at"] = "2026-07-27T12:00:00+00:00"
        cursor_state["universe_generation"] = 1
        _, new_state, refreshed = discovery_daemon.refresh_universe_if_stale(
            conn, cursor_state, refresh_interval_seconds=86400, now="2026-07-27T12:05:00+00:00",
        )
        assert refreshed is False
        assert new_state["universe_generation"] == 1  # unchanged, not incremented


class TestRunCycle:
    def test_correct_chunk_screened_and_upserted(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe",
                             lambda **k: ["A", "B", "C", "D"])
        monkeypatch.setattr(discovery_daemon.discovery_scan, "get_universe_price_band", lambda: (1.0, 100.0))
        seen_chunks = []

        def fake_screen(tickers, min_price, max_price, return_all=False):
            seen_chunks.append(list(tickers))
            return [
                {"ticker": t, "price": 10.0, "rsi": 55.0, "volume_ratio": 1.0, "macd_hist": 0.1, "in_band": True}
                for t in tickers
            ]
        monkeypatch.setattr(discovery_daemon.discovery_screen, "screen_tickers", fake_screen)

        config = dict(discovery_daemon.DEFAULTS)
        config["chunk_size"] = 2
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state = discovery_daemon.run_cycle(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")

        assert seen_chunks == [["A", "B"]]
        assert cursor_state["last_cycle_status"] == "ok"
        assert cursor_state["cursor_position"] == 2
        assert cursor_state["cycles_completed_lifetime"] == 1
        stats = discovery_db.pool_stats(conn)
        assert stats["total_tracked"] == 2

    def test_cursor_persisted_across_calls(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe",
                             lambda **k: ["A", "B", "C", "D"])
        monkeypatch.setattr(discovery_daemon.discovery_scan, "get_universe_price_band", lambda: (1.0, 100.0))
        monkeypatch.setattr(discovery_daemon.discovery_screen, "screen_tickers",
                             lambda tickers, min_price, max_price, return_all=False: [
                                 {"ticker": t, "price": 10.0, "in_band": True} for t in tickers
                             ])
        config = dict(discovery_daemon.DEFAULTS)
        config["chunk_size"] = 2
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state = discovery_daemon.run_cycle(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")
        assert cursor_state["cursor_position"] == 2
        cursor_state = discovery_daemon.run_cycle(conn, cursor_state, config, now="2026-07-27T12:02:00+00:00")
        assert cursor_state["cursor_position"] == 0  # wrapped after covering all 4
        assert cursor_state["full_passes_completed"] == 1

    def test_empty_universe_reports_status_without_crashing(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon.universe_scan, "fetch_broad_universe", lambda **k: [])
        monkeypatch.setattr(discovery_daemon.discovery_scan, "get_universe_price_band", lambda: (1.0, 100.0))
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state = discovery_daemon.run_cycle(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")
        assert cursor_state["last_cycle_status"] == "no_universe"


class TestMaybeConfirmNews:
    def test_noop_before_interval_elapsed(self, conn, monkeypatch):
        calls = []
        monkeypatch.setattr(discovery_daemon.discovery_scan, "confirm_with_news",
                             lambda candidates, top_n=6: calls.append(1) or candidates)
        config = dict(discovery_daemon.DEFAULTS)
        config["finbert_confirm_interval_seconds"] = 1800
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["last_finbert_confirmation_at"] = "2026-07-27T12:00:00+00:00"
        discovery_daemon.maybe_confirm_news(conn, cursor_state, config, now="2026-07-27T12:10:00+00:00")
        assert calls == []

    def test_runs_and_records_after_interval(self, conn, monkeypatch):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "volume_ratio": 1.5, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T12:00:00+00:00",
        )
        monkeypatch.setattr(discovery_daemon.discovery_scan, "confirm_with_news",
                             lambda candidates, top_n=6: [
                                 dict(c, sentiment=0.4, news_headline="AAA news") for c in candidates
                             ])
        config = dict(discovery_daemon.DEFAULTS)
        config["finbert_confirm_interval_seconds"] = 1800
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["last_finbert_confirmation_at"] = "2026-07-27T11:00:00+00:00"
        new_state = discovery_daemon.maybe_confirm_news(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")
        assert new_state["last_finbert_confirmation_at"] == "2026-07-27T12:00:00+00:00"
        row = conn.execute("SELECT sentiment, news_headline FROM candidates WHERE ticker='AAA'").fetchone()
        assert row["sentiment"] == 0.4
        assert row["news_headline"] == "AAA news"

    def test_exception_from_confirm_with_news_does_not_propagate(self, conn, monkeypatch):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "volume_ratio": 1.5, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T12:00:00+00:00",
        )

        def boom(candidates, top_n=6):
            raise ConnectionError("legend-of-macs.local unreachable")
        monkeypatch.setattr(discovery_daemon.discovery_scan, "confirm_with_news", boom)
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        # should not raise
        new_state = discovery_daemon.maybe_confirm_news(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")
        assert new_state["last_finbert_confirmation_at"] == "2026-07-27T12:00:00+00:00"
        assert new_state["last_finbert_failure_at"] == "2026-07-27T12:00:00+00:00"

    # ── Real success/failure tracking (2026-08-10) ─────────────────────────
    # last_finbert_confirmation_at updates on every attempt regardless of
    # outcome (it's the cadence gate) -- confirmed live this looked freshly
    # "healthy" the whole week the real worker was unreachable ~90-98% of
    # the time, because score_sentiment()/score_sentiment_batch() fall back
    # to the keyword scorer silently, no exception ever reaches
    # maybe_confirm_news's try/except. news_collector.LAST_WORKER_CALL_OK is
    # the real signal, set on every worker round-trip regardless of whether
    # the caller ends up using the result or the fallback.

    def test_records_success_when_worker_actually_responded(self, conn, monkeypatch):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "volume_ratio": 1.5, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T12:00:00+00:00",
        )

        def fake_confirm(candidates, top_n=6):
            discovery_daemon.discovery_scan.news_collector.LAST_WORKER_CALL_OK = True
            return [dict(c, sentiment=0.4, news_headline="AAA news") for c in candidates]
        monkeypatch.setattr(discovery_daemon.discovery_scan, "confirm_with_news", fake_confirm)
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        new_state = discovery_daemon.maybe_confirm_news(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")
        assert new_state["last_finbert_success_at"] == "2026-07-27T12:00:00+00:00"
        assert new_state["last_finbert_failure_at"] is None

    def test_records_failure_when_worker_silently_fell_back(self, conn, monkeypatch):
        """The dangerous case: confirm_with_news() returns cleanly (no
        exception -- fallback scores look like valid data), but the worker
        itself never actually responded."""
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "volume_ratio": 1.5, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T12:00:00+00:00",
        )

        def fake_confirm(candidates, top_n=6):
            discovery_daemon.discovery_scan.news_collector.LAST_WORKER_CALL_OK = False
            return [dict(c, sentiment=0.4, news_headline="AAA news") for c in candidates]  # keyword fallback
        monkeypatch.setattr(discovery_daemon.discovery_scan, "confirm_with_news", fake_confirm)
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        new_state = discovery_daemon.maybe_confirm_news(conn, cursor_state, config, now="2026-07-27T12:00:00+00:00")
        assert new_state["last_finbert_failure_at"] == "2026-07-27T12:00:00+00:00"
        assert new_state["last_finbert_success_at"] is None


class TestEnrichCandidateFundamentals:
    def test_writes_sector_industry_market_cap(self, conn):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00+00:00",
        )
        count = discovery_daemon.enrich_candidate_fundamentals(
            conn, ["AAA"],
            fetch_fn=lambda t: {"sector": "Technology", "industry": "Software", "market_cap": 5e9},
        )
        assert count == 1
        row = conn.execute("SELECT sector, market_cap FROM candidates WHERE ticker='AAA'").fetchone()
        assert row["sector"] == "Technology"
        assert row["market_cap"] == 5e9

    def test_one_bad_ticker_does_not_block_the_rest(self, conn):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "GOOD", "price": 10.0, "in_band": True},
                   {"ticker": "BAD", "price": 5.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00+00:00",
        )

        def fetch(t):
            if t == "BAD":
                raise ConnectionError("yfinance unreachable")
            return {"sector": "Technology", "industry": "Software", "market_cap": 1e9}

        count = discovery_daemon.enrich_candidate_fundamentals(conn, ["BAD", "GOOD"], fetch_fn=fetch)
        assert count == 1
        assert conn.execute("SELECT sector FROM candidates WHERE ticker='GOOD'").fetchone()["sector"] == "Technology"
        assert conn.execute("SELECT sector FROM candidates WHERE ticker='BAD'").fetchone()["sector"] is None

    def test_empty_fetch_result_leaves_row_null_not_written(self, conn):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00+00:00",
        )
        count = discovery_daemon.enrich_candidate_fundamentals(
            conn, ["AAA"], fetch_fn=lambda t: {"sector": None, "industry": None, "market_cap": None},
        )
        assert count == 0


class TestMaybeEnrichFundamentals:
    def test_noop_before_interval_elapsed(self, conn, monkeypatch):
        calls = []
        monkeypatch.setattr(discovery_daemon, "enrich_candidate_fundamentals",
                             lambda conn, tickers, **k: calls.append(tickers) or 0)
        config = dict(discovery_daemon.DEFAULTS)
        config["fundamentals_enrich_interval_seconds"] = 900
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["last_fundamentals_enrich_at"] = "2026-08-13T12:00:00+00:00"
        discovery_daemon.maybe_enrich_fundamentals(conn, cursor_state, config, now="2026-08-13T12:05:00+00:00")
        assert calls == []

    def test_only_targets_in_band_null_sector_candidates(self, conn, monkeypatch):
        discovery_db.upsert_candidates(
            conn, [
                {"ticker": "NEEDS_IT", "price": 10.0, "in_band": True},
                {"ticker": "OUT_OF_BAND", "price": 5.0, "in_band": False},
            ],
            universe_generation=1, screened_at="2026-08-13T12:00:00+00:00",
        )
        discovery_db.upsert_candidates(
            conn, [{"ticker": "ALREADY_TAGGED", "price": 8.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00+00:00",
        )
        discovery_db.record_fundamentals(conn, "ALREADY_TAGGED", "Healthcare", "Biotech", 2e9)

        calls = []
        monkeypatch.setattr(discovery_daemon, "enrich_candidate_fundamentals",
                             lambda conn, tickers, **k: calls.append(list(tickers)) or len(tickers))
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        discovery_daemon.maybe_enrich_fundamentals(conn, cursor_state, config, now="2026-08-13T12:10:00+00:00")
        assert calls == [["NEEDS_IT"]]

    def test_records_timestamp_after_run(self, conn, monkeypatch):
        monkeypatch.setattr(discovery_daemon, "enrich_candidate_fundamentals", lambda conn, tickers, **k: 0)
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        new_state = discovery_daemon.maybe_enrich_fundamentals(
            conn, cursor_state, config, now="2026-08-13T12:00:00+00:00",
        )
        assert new_state["last_fundamentals_enrich_at"] == "2026-08-13T12:00:00+00:00"

    def test_exception_does_not_propagate(self, conn, monkeypatch):
        def boom(conn, tickers, **k):
            raise ConnectionError("yfinance unreachable")
        monkeypatch.setattr(discovery_daemon, "enrich_candidate_fundamentals", boom)
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00+00:00",
        )
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        # should not raise
        new_state = discovery_daemon.maybe_enrich_fundamentals(
            conn, cursor_state, config, now="2026-08-13T12:00:00+00:00",
        )
        assert new_state["last_fundamentals_enrich_at"] == "2026-08-13T12:00:00+00:00"


class TestMaybeScanEdgarFilings:
    """2026-08-13: BWMN data-gap fix -- same cadence-gate pattern as
    TestMaybeEnrichFundamentals above."""

    def test_noop_before_interval_elapsed(self, conn, monkeypatch):
        calls = []
        monkeypatch.setattr(discovery_daemon.edgar_scan, "scan_tickers",
                             lambda tickers, **k: calls.append(tickers) or {})
        config = dict(discovery_daemon.DEFAULTS)
        config["edgar_scan_interval_seconds"] = 21600
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        cursor_state["last_edgar_scan_at"] = "2026-08-13T12:00:00+00:00"
        discovery_daemon.maybe_scan_edgar_filings(conn, cursor_state, config, now="2026-08-13T13:00:00+00:00")
        assert calls == []

    def test_writes_flag_for_hit_and_clears_for_miss(self, conn, monkeypatch):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True},
                   {"ticker": "BBB", "price": 5.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00+00:00",
        )
        monkeypatch.setattr(discovery_daemon.edgar_scan, "get_ticker_cik_map", lambda now=None: {})
        monkeypatch.setattr(
            discovery_daemon.edgar_scan, "scan_tickers",
            lambda tickers, **k: {"AAA": [{"ticker": "AAA", "form": "8-K", "filing_date": "2026-08-11",
                                            "items": ["2.01"], "accession_number": "x",
                                            "filing_url": "https://sec.gov/x"}]},
        )
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        new_state = discovery_daemon.maybe_scan_edgar_filings(
            conn, cursor_state, config, now="2026-08-13T12:10:00+00:00",
        )
        assert new_state["last_edgar_scan_at"] == "2026-08-13T12:10:00+00:00"
        rows = {r["ticker"]: r["ma_filing_flag"] for r in conn.execute("SELECT ticker, ma_filing_flag FROM candidates")}
        assert rows["AAA"] == "8-K item 2.01 filed 2026-08-11"
        assert rows["BBB"] is None
        checked = {r["ticker"]: r["ma_filing_checked_at"] for r in conn.execute("SELECT ticker, ma_filing_checked_at FROM candidates")}
        assert checked["BBB"] == "2026-08-13T12:10:00+00:00"  # clean check still recorded

    def test_targets_never_checked_before_previously_checked(self, conn, monkeypatch):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "OLD_CHECK", "price": 10.0, "in_band": True},
                   {"ticker": "NEVER_CHECKED", "price": 5.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00+00:00",
        )
        discovery_db.record_ma_filing_check(conn, "OLD_CHECK", None, "2026-08-01T00:00:00+00:00")

        monkeypatch.setattr(discovery_daemon.edgar_scan, "get_ticker_cik_map", lambda now=None: {})
        calls = []
        monkeypatch.setattr(discovery_daemon.edgar_scan, "scan_tickers",
                             lambda tickers, **k: calls.append(list(tickers)) or {})
        config = dict(discovery_daemon.DEFAULTS)
        config["edgar_scan_max_per_cycle"] = 1
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        discovery_daemon.maybe_scan_edgar_filings(conn, cursor_state, config, now="2026-08-13T12:10:00+00:00")
        assert calls == [["NEVER_CHECKED"]]

    def test_exception_does_not_propagate(self, conn, monkeypatch):
        discovery_db.upsert_candidates(
            conn, [{"ticker": "AAA", "price": 10.0, "in_band": True}],
            universe_generation=1, screened_at="2026-08-13T12:00:00+00:00",
        )

        def boom(now=None):
            raise ConnectionError("sec.gov unreachable")
        monkeypatch.setattr(discovery_daemon.edgar_scan, "get_ticker_cik_map", boom)
        config = dict(discovery_daemon.DEFAULTS)
        cursor_state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        # should not raise
        new_state = discovery_daemon.maybe_scan_edgar_filings(
            conn, cursor_state, config, now="2026-08-13T12:00:00+00:00",
        )
        assert new_state["last_edgar_scan_at"] == "2026-08-13T12:00:00+00:00"


class TestDaemonHealth:
    # Downstream-flow check (see TestDownstreamFlowCheck) is gated on
    # executor._is_regular_trading_hours() -- forced False here so these
    # pre-existing tests stay deterministic and never touch the real
    # default-path state/discovery_pool.db / state/trader.db regardless of
    # what time it actually is when the suite runs.
    @pytest.fixture(autouse=True)
    def _off_hours(self, monkeypatch):
        monkeypatch.setattr(discovery_daemon.executor, "_is_regular_trading_hours", lambda: False)

    def test_missing_file_unhealthy(self, tmp_path, monkeypatch):
        result = discovery_daemon.daemon_health(cursor_state_path=tmp_path / "missing.json")
        assert result["healthy"] is False

    def test_fresh_is_healthy(self, tmp_path):
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["last_cycle_completed_at"] = "2026-07-27T12:00:00+00:00"
        state["last_cycle_status"] = "ok"
        discovery_daemon.save_cursor_state(state, path=path)
        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now="2026-07-27T12:05:00+00:00", cursor_state_path=path,
        )
        assert result["healthy"] is True

    def test_stale_past_threshold_unhealthy(self, tmp_path):
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["last_cycle_completed_at"] = "2026-07-27T12:00:00+00:00"
        state["last_cycle_status"] = "ok"
        discovery_daemon.save_cursor_state(state, path=path)
        result = discovery_daemon.daemon_health(
            max_staleness_seconds=60, now="2026-07-27T12:05:00+00:00", cursor_state_path=path,
        )
        assert result["healthy"] is False

    def test_downstream_flow_not_applicable_off_hours(self, tmp_path):
        """Off-hours, downstream_flow is None (not applicable) and never
        drags healthy down -- there's no tick loop running to promote
        anything regardless of what the pool holds."""
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["last_cycle_completed_at"] = "2026-07-27T12:00:00+00:00"
        state["last_cycle_status"] = "ok"
        discovery_daemon.save_cursor_state(state, path=path)
        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now="2026-07-27T12:05:00+00:00", cursor_state_path=path,
        )
        assert result["downstream_flow"] is None
        assert result["healthy"] is True

    # ── sentiment_worker_ok (2026-08-10) — separate from the loop's own
    # `healthy` flag on purpose: a degraded sentiment worker doesn't mean
    # the discovery loop itself is broken. ──────────────────────────────

    def test_sentiment_worker_ok_none_when_never_recorded(self, tmp_path):
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["last_cycle_completed_at"] = "2026-07-27T12:00:00+00:00"
        discovery_daemon.save_cursor_state(state, path=path)
        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now="2026-07-27T12:05:00+00:00", cursor_state_path=path,
        )
        assert result["sentiment_worker_ok"] is None

    def test_sentiment_worker_ok_true_when_success_is_most_recent(self, tmp_path):
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["last_cycle_completed_at"] = "2026-07-27T12:00:00+00:00"
        state["last_finbert_failure_at"] = "2026-07-27T10:00:00+00:00"
        state["last_finbert_success_at"] = "2026-07-27T11:00:00+00:00"
        discovery_daemon.save_cursor_state(state, path=path)
        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now="2026-07-27T12:05:00+00:00", cursor_state_path=path,
        )
        assert result["sentiment_worker_ok"] is True

    def test_sentiment_worker_ok_false_when_failure_is_most_recent(self, tmp_path):
        """The exact confirmed-live scenario: worker's been down for a
        week, last real success is stale, most recent attempt failed."""
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["last_cycle_completed_at"] = "2026-08-10T12:00:00+00:00"
        state["last_finbert_success_at"] = "2026-08-03T09:00:00+00:00"
        state["last_finbert_failure_at"] = "2026-08-10T11:55:00+00:00"
        discovery_daemon.save_cursor_state(state, path=path)
        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now="2026-08-10T12:05:00+00:00", cursor_state_path=path,
        )
        assert result["sentiment_worker_ok"] is False


class TestDownstreamFlowCheck:
    """2026-08-12: daemon_health() previously only checked the screening
    cycle itself -- it had no way to notice the pool filling up with real
    candidates while promote_candidates.py silently stopped landing any of
    them in watchlist_candidates (the 2026-08-03 merge_discoveries.py
    regex bug's failure shape exactly: 68-81% of the watchlist silently
    rejected for a week before anyone caught it by reading the journal).
    All tests here force market hours True and point both DBs at tmp_path
    scratch files -- never the real state/discovery_pool.db or
    state/trader.db."""

    @pytest.fixture(autouse=True)
    def _market_hours(self, monkeypatch):
        monkeypatch.setattr(discovery_daemon.executor, "_is_regular_trading_hours", lambda: True)

    def _cursor_path(self, tmp_path, now):
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["last_cycle_completed_at"] = now
        state["last_cycle_status"] = "ok"
        discovery_daemon.save_cursor_state(state, path=path)
        return path

    def test_no_fresh_candidates_is_not_applicable(self, tmp_path):
        """Pool has nothing fresh in-band right now -- nothing to promote,
        so a quiet watchlist isn't evidence of a break."""
        now = "2026-08-12T15:00:00+00:00"
        pool_db = tmp_path / "pool.db"
        trader_db_path = tmp_path / "trader.db"
        discovery_db.get_conn(pool_db).close()  # empty pool, no candidates at all
        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now=now, cursor_state_path=self._cursor_path(tmp_path, now),
            pool_db_path=pool_db, trader_db_path=trader_db_path,
        )
        assert result["downstream_flow"] is None
        assert result["healthy"] is True

    def test_fresh_candidates_but_no_watchlist_touch_ever_is_unhealthy(self, tmp_path, monkeypatch):
        """The actual regression this closes: pool has real supply, but
        nothing pool-sourced has ever landed in watchlist_candidates."""
        now = "2026-08-12T15:00:00+00:00"
        pool_db = tmp_path / "pool.db"
        trader_db_path = tmp_path / "trader.db"
        pool_conn = discovery_db.get_conn(pool_db)
        discovery_db.upsert_candidates(pool_conn, [
            {"ticker": "AAA", "price": 10.0, "in_band": True},
        ], universe_generation=1, screened_at=now)
        pool_conn.close()

        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now=now, cursor_state_path=self._cursor_path(tmp_path, now),
            pool_db_path=pool_db, trader_db_path=trader_db_path,
        )
        assert result["downstream_flow"]["ok"] is False
        assert result["downstream_flow"]["fresh_in_band_count"] == 1
        assert result["downstream_flow"]["last_watchlist_touch"] is None
        assert result["healthy"] is False

    def test_fresh_candidates_with_recent_watchlist_touch_is_healthy(self, tmp_path):
        now = "2026-08-12T15:00:00+00:00"
        pool_db = tmp_path / "pool.db"
        trader_db_path = tmp_path / "trader.db"
        pool_conn = discovery_db.get_conn(pool_db)
        discovery_db.upsert_candidates(pool_conn, [
            {"ticker": "AAA", "price": 10.0, "in_band": True},
        ], universe_generation=1, screened_at=now)
        pool_conn.close()

        conn = trader_db.get_conn(trader_db_path)
        trader_db.upsert_watchlist_candidate(
            conn, ticker="AAA", price=10.0, source="discovery_pool gen 1",
            now="2026-08-12T14:55:00+00:00",  # 5 min before `now`, well under the 30 min bar
        )
        conn.close()

        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now=now, cursor_state_path=self._cursor_path(tmp_path, now),
            pool_db_path=pool_db, trader_db_path=trader_db_path,
        )
        assert result["downstream_flow"]["ok"] is True
        assert result["healthy"] is True

    def test_fresh_candidates_with_stale_watchlist_touch_is_unhealthy(self, tmp_path):
        now = "2026-08-12T15:00:00+00:00"
        pool_db = tmp_path / "pool.db"
        trader_db_path = tmp_path / "trader.db"
        pool_conn = discovery_db.get_conn(pool_db)
        discovery_db.upsert_candidates(pool_conn, [
            {"ticker": "AAA", "price": 10.0, "in_band": True},
        ], universe_generation=1, screened_at=now)
        pool_conn.close()

        conn = trader_db.get_conn(trader_db_path)
        trader_db.upsert_watchlist_candidate(
            conn, ticker="AAA", price=10.0, source="discovery_pool gen 1",
            now="2026-08-12T14:00:00+00:00",  # 60 min before `now`, past the 30 min default bar
        )
        conn.close()

        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now=now, cursor_state_path=self._cursor_path(tmp_path, now),
            pool_db_path=pool_db, trader_db_path=trader_db_path,
        )
        assert result["downstream_flow"]["ok"] is False
        assert result["healthy"] is False

    def test_non_pool_sourced_watchlist_touch_does_not_count(self, tmp_path):
        """A recent manual/RSS-sourced add shouldn't mask the pool
        specifically failing to reach the watchlist."""
        now = "2026-08-12T15:00:00+00:00"
        pool_db = tmp_path / "pool.db"
        trader_db_path = tmp_path / "trader.db"
        pool_conn = discovery_db.get_conn(pool_db)
        discovery_db.upsert_candidates(pool_conn, [
            {"ticker": "AAA", "price": 10.0, "in_band": True},
        ], universe_generation=1, screened_at=now)
        pool_conn.close()

        conn = trader_db.get_conn(trader_db_path)
        trader_db.upsert_watchlist_candidate(
            conn, ticker="BBB", price=5.0, source="discoveries_md",
            now="2026-08-12T14:59:00+00:00",
        )
        conn.close()

        result = discovery_daemon.daemon_health(
            max_staleness_seconds=600, now=now, cursor_state_path=self._cursor_path(tmp_path, now),
            pool_db_path=pool_db, trader_db_path=trader_db_path,
        )
        assert result["downstream_flow"]["ok"] is False
        assert result["downstream_flow"]["last_watchlist_touch"] is None


class TestCheckHealthCLI:
    """Locks in the --check-health exit-code contract a health-check cron
    depends on: exit 0 when healthy, nonzero when not. No daemon loop, no
    network -- just daemon_health() + a JSON print, so this (unlike the
    while-True loop) is worth testing directly through main()."""

    def _run(self, monkeypatch, cursor_state_path, capsys):
        monkeypatch.setattr(discovery_daemon, "CURSOR_STATE_PATH", cursor_state_path)
        # Off-hours by default so this class's pre-existing tests don't
        # depend on the downstream check / real DB paths -- matches
        # TestDaemonHealth's approach above.
        monkeypatch.setattr(discovery_daemon.executor, "_is_regular_trading_hours", lambda: False)
        monkeypatch.setattr(sys, "argv", ["discovery_daemon.py", "--check-health"])
        with pytest.raises(SystemExit) as exc_info:
            discovery_daemon.main()
        captured = capsys.readouterr()
        return exc_info.value.code, json.loads(captured.out)

    def test_healthy_exits_zero(self, tmp_path, monkeypatch, capsys):
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["last_cycle_completed_at"] = discovery_daemon._now_iso()
        state["last_cycle_status"] = "ok"
        discovery_daemon.save_cursor_state(state, path=path)
        code, payload = self._run(monkeypatch, path, capsys)
        assert code == 0
        assert payload["healthy"] is True

    def test_missing_state_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        code, payload = self._run(monkeypatch, tmp_path / "nope.json", capsys)
        assert code != 0
        assert payload["healthy"] is False


class TestCursorStatePersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "state.json"
        state = dict(discovery_daemon.DEFAULT_CURSOR_STATE)
        state["cursor_position"] = 42
        discovery_daemon.save_cursor_state(state, path=path)
        loaded = discovery_daemon.load_cursor_state(path=path)
        assert loaded["cursor_position"] == 42

    def test_load_missing_file_returns_defaults(self, tmp_path):
        loaded = discovery_daemon.load_cursor_state(path=tmp_path / "nope.json")
        assert loaded == discovery_daemon.DEFAULT_CURSOR_STATE
