#!/usr/bin/env python3
"""
Unit tests for scripts/research_escalation.py -- the selection query for
the research-escalation pipeline stage. Pure read/report, real sqlite3
under tmp_path (same convention as test_trader_write.py) -- no mocking of
sessions_send/researcher needed since this script never calls either
itself (that happens inside the stonks-research-escalation cron's own
agentTurn, see skills/research-escalation.md).
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import research_escalation  # noqa: E402
import trader_db  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps({
        "watchlist": {
            "interest_min_score": 2,
            "research_escalation": {"cooldown_hours": 6, "cap": 3, "max_rounds": 2},
        },
    }))
    monkeypatch.setattr(research_escalation, "PARAMS_PATH", params_path)
    db_path = tmp_path / "trader.db"
    return {"params_path": params_path, "db_path": db_path}


def _seed(db_path, ticker, tree_match_state, signals=None, now="2026-08-13T10:00:00+00:00"):
    conn = trader_db.get_conn(db_path)
    try:
        trader_db.upsert_watchlist_candidate(conn, ticker=ticker, **(signals or {}))
        trader_db.mark_candidates_evaluated(conn, [ticker], now=now, tree_match_state={ticker: tree_match_state})
    finally:
        conn.close()


class TestLoadConfig:
    def test_reads_params_json(self, env):
        config = research_escalation._load_config()
        assert config == {
            "interest_min_score": 2, "cooldown_hours": 6, "cap": 3, "max_rounds": 2,
        }

    def test_falls_back_to_defaults_when_missing(self, env):
        env["params_path"].write_text(json.dumps({}))
        config = research_escalation._load_config()
        assert config["interest_min_score"] == trader_db.DEFAULT_INTEREST_MIN_SCORE
        assert config["cooldown_hours"] == research_escalation.DEFAULT_COOLDOWN_HOURS
        assert config["cap"] == research_escalation.DEFAULT_CAP
        assert config["max_rounds"] is None


class TestSelect:
    def test_selects_qualifying_candidate(self, env):
        _seed(env["db_path"], "AAA", "watch", {"sentiment": 0.6})
        result = research_escalation.select(db_path=env["db_path"])
        assert [c["ticker"] for c in result["candidates"]] == ["AAA"]
        assert result["interest_min_score"] == 2
        assert result["cooldown_hours"] == 6
        assert result["cap"] == 3
        assert result["max_rounds"] == 2

    def test_excludes_active_match(self, env):
        _seed(env["db_path"], "AAA", "active", {"sentiment": 0.6, "volume_ratio": 2.5})
        result = research_escalation.select(db_path=env["db_path"])
        assert result["candidates"] == []

    def test_excludes_below_interest_floor(self, env):
        _seed(env["db_path"], "AAA", "watch")  # no signals -> score 0
        result = research_escalation.select(db_path=env["db_path"])
        assert result["candidates"] == []

    def test_cli_overrides_win_over_params_json(self, env):
        _seed(env["db_path"], "AAA", "watch", {"volume_ratio": 2.5})  # score 1
        result = research_escalation.select(db_path=env["db_path"], interest_min_score=0)
        assert [c["ticker"] for c in result["candidates"]] == ["AAA"]
        assert result["interest_min_score"] == 0

    def test_cap_applied(self, env):
        _seed(env["db_path"], "AAA", "watch", {"sentiment": 0.6})
        _seed(env["db_path"], "BBB", "watch", {"sentiment": 0.6})
        result = research_escalation.select(db_path=env["db_path"], cap=1)
        assert len(result["candidates"]) == 1

    def test_cooldown_excludes_recently_researched(self, env):
        _seed(env["db_path"], "AAA", "watch", {"sentiment": 0.6}, now="2026-08-13T10:00:00+00:00")
        conn = trader_db.get_conn(env["db_path"])
        trader_db.record_watchlist_research(conn, "AAA", confidence=0.5, now="2026-08-13T10:30:00+00:00")
        conn.close()
        result = research_escalation.select(db_path=env["db_path"], now="2026-08-13T11:00:00+00:00")
        assert result["candidates"] == []
