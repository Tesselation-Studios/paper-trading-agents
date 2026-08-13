#!/usr/bin/env python3
"""
Unit tests for scripts/promote_candidates.py -- the discovery-pool ->
watchlist_candidates table bridge. Mirrors test_merge_discoveries.py's
TestMerge shape: isolated trader_db.DB_PATH/params.json under tmp_path,
plus a seeded pool db under tmp_path (no mocking needed for the pool --
real sqlite3). Note: promote()'s own db_path param is the discovery POOL
db (state/discovery_pool.db) -- a different database from trader_db.py's
state/trader.db, which insert_into_watchlist() always uses via its own
default (promote() never threads its db_path through to that call, since
they're different databases). trader_db.DB_PATH must be monkeypatched
separately for that reason.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import promote_candidates  # noqa: E402
import merge_discoveries  # noqa: E402
import trader_db  # noqa: E402
import discovery_db  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps({"watchlist": {"max_size": 30},
                                        "discovery_daemon": {"promote_top_n": 5, "promote_max_age_seconds": 10800}}))

    monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
    monkeypatch.setattr(promote_candidates, "PARAMS_PATH", params_path)
    monkeypatch.setattr(merge_discoveries, "PARAMS_PATH", params_path)

    conn = trader_db.get_conn(tmp_path / "trader.db")
    trader_db.upsert_position(conn, ticker="NVDA", shares=1.0, entry_price=500.0, entry_time="t1")
    conn.close()

    db_path = tmp_path / "pool.db"
    return {"params_path": params_path, "db_path": db_path}


def _candidates():
    conn = trader_db.get_conn()
    try:
        return {c["ticker"]: c for c in trader_db.get_watchlist_candidates(conn)}
    finally:
        conn.close()


def _seed(db_path, tickers_volume_ratios, generation=1, screened_at="2026-07-27T12:00:00+00:00"):
    conn = discovery_db.get_conn(db_path)
    discovery_db.upsert_universe_snapshot(
        conn, [t for t, _ in tickers_volume_ratios], generation=generation, fetched_at=screened_at,
    )
    discovery_db.upsert_candidates(
        conn,
        [{"ticker": t, "price": 10.0, "volume_ratio": v, "in_band": True} for t, v in tickers_volume_ratios],
        universe_generation=generation, screened_at=screened_at,
    )
    conn.close()


class TestSelectPromotable:
    def test_returns_fresh_in_band_ranked(self, env):
        _seed(env["db_path"], [("AAA", 1.0), ("BBB", 2.0)])
        conn = discovery_db.get_conn(env["db_path"])
        result = promote_candidates.select_promotable(conn, top_n=5, max_age_seconds=86400,
                                                        now="2026-07-27T12:05:00+00:00")
        conn.close()
        assert [c["ticker"] for c in result] == ["BBB", "AAA"]

    def test_min_market_cap_excludes_sub_floor(self, env):
        _seed(env["db_path"], [("SMALL", 1.0), ("BIG", 2.0)])
        conn = discovery_db.get_conn(env["db_path"])
        discovery_db.record_fundamentals(conn, "SMALL", "Technology", "Software", 50_000_000.0)
        discovery_db.record_fundamentals(conn, "BIG", "Technology", "Software", 5_000_000_000.0)
        result = promote_candidates.select_promotable(
            conn, top_n=5, max_age_seconds=86400, now="2026-07-27T12:05:00+00:00",
            min_market_cap=300_000_000,
        )
        conn.close()
        assert [c["ticker"] for c in result] == ["BIG"]

    def test_min_market_cap_passes_through_unknown_market_cap(self, env):
        """Not yet enriched -- best-effort trim, not a hard data-
        completeness gate, so an unknown market_cap isn't excluded."""
        _seed(env["db_path"], [("UNENRICHED", 1.0)])
        conn = discovery_db.get_conn(env["db_path"])
        result = promote_candidates.select_promotable(
            conn, top_n=5, max_age_seconds=86400, now="2026-07-27T12:05:00+00:00",
            min_market_cap=300_000_000,
        )
        conn.close()
        assert [c["ticker"] for c in result] == ["UNENRICHED"]

    def test_min_market_cap_none_disables_filter(self, env):
        _seed(env["db_path"], [("SMALL", 1.0)])
        conn = discovery_db.get_conn(env["db_path"])
        discovery_db.record_fundamentals(conn, "SMALL", "Technology", "Software", 50_000_000.0)
        result = promote_candidates.select_promotable(
            conn, top_n=5, max_age_seconds=86400, now="2026-07-27T12:05:00+00:00", min_market_cap=None,
        )
        conn.close()
        assert [c["ticker"] for c in result] == ["SMALL"]


FIXED_NOW = "2026-07-27T12:05:00+00:00"  # just after _seed's default screened_at -- keeps
# these tests deterministic regardless of real wall-clock date (promote() defaults to
# real time when `now` isn't passed; a prior version of these tests relied on that
# default staying "close enough" to the hardcoded seed timestamp and broke the moment
# the real date rolled past it -- see 2026-07-28 fix).


class TestPromote:
    def test_merges_pool_candidates_into_watchlist(self, env):
        _seed(env["db_path"], [("ZZZ", 1.5)])
        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=86400, now=FIXED_NOW)
        assert result["merged"] == ["ZZZ"]
        candidates = _candidates()
        assert candidates["ZZZ"]["source"] == "discovery_pool gen 1"

    def test_dedup_against_existing_watchlist(self, env):
        conn = trader_db.get_conn()
        trader_db.upsert_watchlist_candidate(conn, ticker="AAA", source="prior")
        conn.close()
        _seed(env["db_path"], [("AAA", 1.0), ("BBB", 2.0)])
        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=86400, now=FIXED_NOW)
        assert result["merged"] == ["BBB"]
        assert "AAA" in result["skipped"]

    def test_respects_max_size(self, env):
        env["params_path"].write_text(json.dumps({"watchlist": {"max_size": 1}}))
        _seed(env["db_path"], [("AAA", 2.0), ("BBB", 1.0)])
        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=86400, now=FIXED_NOW)
        assert result["merged"] == ["AAA"]
        assert any("max_size 1 reached" in s for s in result["skipped"])

    def test_dry_run_does_not_write(self, env):
        _seed(env["db_path"], [("ZZZ", 1.5)])
        result = promote_candidates.promote(db_path=env["db_path"], dry_run=True, max_age_seconds=86400, now=FIXED_NOW)
        assert result["merged"] == ["ZZZ"]
        assert _candidates() == {}

    def test_empty_pool_is_noop(self, env):
        result = promote_candidates.promote(db_path=env["db_path"], now=FIXED_NOW)
        assert result["merged"] == []
        assert result["pool_candidates_considered"] == 0

    def test_respects_max_age_seconds(self, env):
        _seed(env["db_path"], [("STALE", 1.0)], screened_at="2020-01-01T00:00:00+00:00")
        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=3600, now=FIXED_NOW)
        assert result["merged"] == []
        assert result["pool_candidates_considered"] == 0

    def test_pool_signals_survive_the_promotion(self, env):
        """2026-08-01 fix: promote() passed [c["ticker"] for c in
        candidates], so every signal the daemon had already computed died
        at this boundary and the tick had to re-derive it per candidate."""
        conn = discovery_db.get_conn(env["db_path"])
        discovery_db.upsert_universe_snapshot(conn, ["ZZZ"], generation=1, fetched_at="2026-07-27T12:00:00+00:00")
        discovery_db.upsert_candidates(
            conn, [{"ticker": "ZZZ", "price": 4.20, "rsi": 55.0, "volume_ratio": 6.1,
                    "macd_hist": 0.03, "in_band": True}],
            universe_generation=1, screened_at="2026-07-27T12:00:00+00:00",
        )
        discovery_db.record_news_confirmation(conn, "ZZZ", -0.87, "ZZZ halted", "2026-07-27T12:01:00+00:00")
        conn.close()

        promote_candidates.promote(db_path=env["db_path"], max_age_seconds=86400, now=FIXED_NOW)

        row = _candidates()["ZZZ"]
        assert row["price"] == 4.20
        assert row["rsi"] == 55.0
        assert row["volume_ratio"] == 6.1
        assert row["macd_hist"] == 0.03
        assert row["sentiment"] == -0.87
        assert row["news_headline"] == "ZZZ halted"

    def test_top_n_caps_pool_candidates_considered(self, env):
        _seed(env["db_path"], [("A", 1.0), ("B", 2.0), ("C", 3.0)])
        result = promote_candidates.promote(db_path=env["db_path"], top_n=2, max_age_seconds=86400, now=FIXED_NOW)
        assert result["pool_candidates_considered"] == 2
        assert set(result["merged"]) == {"B", "C"}

    def test_min_market_cap_read_from_params_json(self, env):
        env["params_path"].write_text(json.dumps({
            "watchlist": {"max_size": 30},
            "discovery_daemon": {"promote_top_n": 5, "promote_max_age_seconds": 10800},
            "universe": {"min_market_cap": 300_000_000},
        }))
        _seed(env["db_path"], [("SMALL", 1.0), ("BIG", 2.0)])
        conn = discovery_db.get_conn(env["db_path"])
        discovery_db.record_fundamentals(conn, "SMALL", "Technology", "Software", 50_000_000.0)
        discovery_db.record_fundamentals(conn, "BIG", "Technology", "Software", 5_000_000_000.0)
        conn.close()

        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=86400, now=FIXED_NOW)
        assert result["merged"] == ["BIG"]

    def test_min_market_cap_cli_override_wins_over_params_json(self, env):
        env["params_path"].write_text(json.dumps({
            "watchlist": {"max_size": 30},
            "discovery_daemon": {"promote_top_n": 5, "promote_max_age_seconds": 10800},
            "universe": {"min_market_cap": 300_000_000},
        }))
        _seed(env["db_path"], [("SMALL", 1.0)])
        conn = discovery_db.get_conn(env["db_path"])
        discovery_db.record_fundamentals(conn, "SMALL", "Technology", "Software", 50_000_000.0)
        conn.close()

        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=86400, now=FIXED_NOW,
                                             min_market_cap=0)
        assert result["merged"] == ["SMALL"]

    def test_no_min_market_cap_configured_defaults_to_no_filter(self, env):
        _seed(env["db_path"], [("SMALL", 1.0)])
        conn = discovery_db.get_conn(env["db_path"])
        discovery_db.record_fundamentals(conn, "SMALL", "Technology", "Software", 50_000_000.0)
        conn.close()

        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=86400, now=FIXED_NOW)
        assert result["merged"] == ["SMALL"]
