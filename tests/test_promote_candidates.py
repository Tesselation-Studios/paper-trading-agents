#!/usr/bin/env python3
"""
Unit tests for scripts/promote_candidates.py -- the discovery-pool ->
watchlist.md bridge. Mirrors test_merge_discoveries.py's TestMerge shape:
isolated watchlist.md/params.json under tmp_path, plus a seeded pool db
under tmp_path (no mocking needed for the pool -- real sqlite3).
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import promote_candidates  # noqa: E402
import merge_discoveries  # noqa: E402
import discovery_db  # noqa: E402


DEFAULT_WATCHLIST = """# Watchlist — Growing/Shrinking Candidate List

Format: `TICKER — idle_ticks: N — note`

## Currently Held (always on the list, idle_ticks doesn't apply while open)
- NVDA — open position

## Candidates
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    watchlist_path = tmp_path / "strategies" / "watchlist.md"
    watchlist_path.parent.mkdir()
    watchlist_path.write_text(DEFAULT_WATCHLIST)
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps({"watchlist": {"max_size": 30},
                                        "discovery_daemon": {"promote_top_n": 5, "promote_max_age_seconds": 10800}}))

    monkeypatch.setattr(merge_discoveries, "WATCHLIST_PATH", watchlist_path)
    monkeypatch.setattr(merge_discoveries, "PARAMS_PATH", params_path)
    monkeypatch.setattr(promote_candidates, "PARAMS_PATH", params_path)

    db_path = tmp_path / "pool.db"
    return {"watchlist_path": watchlist_path, "params_path": params_path, "db_path": db_path}


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


class TestPromote:
    def test_merges_pool_candidates_into_watchlist(self, env):
        _seed(env["db_path"], [("ZZZ", 1.5)])
        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=86400)
        assert result["merged"] == ["ZZZ"]
        text = env["watchlist_path"].read_text()
        assert "- ZZZ — idle_ticks: 0 — from discovery_pool gen 1" in text

    def test_dedup_against_existing_watchlist(self, env):
        env["watchlist_path"].write_text(DEFAULT_WATCHLIST + "- AAA — idle_ticks: 0 — from prior\n")
        _seed(env["db_path"], [("AAA", 1.0), ("BBB", 2.0)])
        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=86400)
        assert result["merged"] == ["BBB"]
        assert "AAA" in result["skipped"]

    def test_respects_max_size(self, env):
        env["params_path"].write_text(json.dumps({"watchlist": {"max_size": 1}}))
        _seed(env["db_path"], [("AAA", 2.0), ("BBB", 1.0)])
        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=86400)
        assert result["merged"] == ["AAA"]
        assert any("max_size 1 reached" in s for s in result["skipped"])

    def test_dry_run_does_not_write(self, env):
        _seed(env["db_path"], [("ZZZ", 1.5)])
        before = env["watchlist_path"].read_text()
        result = promote_candidates.promote(db_path=env["db_path"], dry_run=True, max_age_seconds=86400)
        after = env["watchlist_path"].read_text()
        assert result["merged"] == ["ZZZ"]
        assert before == after

    def test_empty_pool_is_noop(self, env):
        result = promote_candidates.promote(db_path=env["db_path"])
        assert result["merged"] == []
        assert result["pool_candidates_considered"] == 0

    def test_respects_max_age_seconds(self, env):
        _seed(env["db_path"], [("STALE", 1.0)], screened_at="2020-01-01T00:00:00+00:00")
        result = promote_candidates.promote(db_path=env["db_path"], max_age_seconds=3600)
        assert result["merged"] == []
        assert result["pool_candidates_considered"] == 0

    def test_top_n_caps_pool_candidates_considered(self, env):
        _seed(env["db_path"], [("A", 1.0), ("B", 2.0), ("C", 3.0)])
        result = promote_candidates.promote(db_path=env["db_path"], top_n=2, max_age_seconds=86400)
        assert result["pool_candidates_considered"] == 2
        assert set(result["merged"]) == {"B", "C"}
