#!/usr/bin/env python3
"""
Unit tests for scripts/merge_discoveries.py — mechanical feed of
discoveries/YYYY-MM-DD.md ticker candidates into the local
watchlist_candidates table.

DISCOVERIES_DIR and PARAMS_PATH are monkeypatched to tmp_path fixtures;
trader_db.DB_PATH is monkeypatched to an isolated tmp_path db (2026-07-28,
migrated off a regex-parsed watchlist.md) — no test ever touches the real
discoveries/ dir, state/trader.db, or params.json.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import merge_discoveries  # noqa: E402
import trader_db  # noqa: E402


def make_discoveries_file(dir_path: Path, date: str, tickers_and_prices):
    """Write a discoveries/<date>.md file with '## TICKER — $price' headers,
    mirroring the real probe-discovery format."""
    lines = [f"# Probe Discovery — {date}", "", "Fresh ticker probes.", "", "---", ""]
    for ticker, price in tickers_and_prices:
        lines.append(f"## {ticker} — ${price}")
        lines.append("- Sector: Test")
        lines.append("- Why interesting: filler text for the test fixture.")
        lines.append("")
    path = dir_path / f"{date}.md"
    path.write_text("\n".join(lines))
    return path


@pytest.fixture
def merge_env(tmp_path, monkeypatch):
    """Isolated discoveries/, trader_db.DB_PATH, and params.json under
    tmp_path. Seeds NVDA as an open position, mirroring the old fixture's
    'Currently Held' concept."""
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()
    db_path = tmp_path / "trader.db"
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps({"watchlist": {"max_size": 30}}))

    monkeypatch.setattr(merge_discoveries, "DISCOVERIES_DIR", discoveries_dir)
    monkeypatch.setattr(merge_discoveries, "PARAMS_PATH", params_path)
    monkeypatch.setattr(trader_db, "DB_PATH", db_path)

    conn = trader_db.get_conn(db_path)
    trader_db.upsert_position(conn, ticker="NVDA", shares=1.0, entry_price=500.0, entry_time="t1")
    conn.close()

    return {"discoveries_dir": discoveries_dir, "params_path": params_path, "db_path": db_path}


def _candidates(db_path):
    conn = trader_db.get_conn(db_path)
    try:
        return {c["ticker"]: c for c in trader_db.get_watchlist_candidates(conn)}
    finally:
        conn.close()


def _seed_candidates(db_path, tickers):
    conn = trader_db.get_conn(db_path)
    for t in tickers:
        trader_db.upsert_watchlist_candidate(conn, ticker=t, source="prior")
    conn.close()


def _seed_closed_position(db_path, ticker):
    conn = trader_db.get_conn(db_path)
    trader_db.upsert_position(conn, ticker=ticker, shares=1.0, entry_price=10.0, entry_time="t1")
    trader_db.close_position(conn, ticker=ticker, closed_at="t2", close_reason="exit", realized_pnl=1.0, realized_return_pct=1.0)
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# extract_candidates
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractCandidates:
    def test_parses_ticker_headers(self):
        text = (
            "# Probe Discovery — 2026-07-22\n\n"
            "## PLTR — $132.34\n- Sector: Tech\n\n"
            "## IOVA — $5.48\n- Sector: Healthcare\n"
        )
        assert merge_discoveries.extract_candidates(text) == ["PLTR", "IOVA"]

    def test_ignores_non_ticker_headers(self):
        text = (
            "## Sector Balance Check\n"
            "Some prose about balance, no ticker here.\n\n"
            "## PLTR — $132.34\n- Sector: Tech\n"
        )
        assert merge_discoveries.extract_candidates(text) == ["PLTR"]

    def test_no_headers_returns_empty(self):
        text = "# Probe Discovery\n\nNo picks worth flagging today.\n"
        assert merge_discoveries.extract_candidates(text) == []

    def test_requires_dollar_sign_after_dash(self):
        # A "## TICKER — note" header without a $ price shouldn't match —
        # this is what naturally excludes non-ticker section headers.
        text = "## WATCH — keep an eye on this\n"
        assert merge_discoveries.extract_candidates(text) == []


# ─────────────────────────────────────────────────────────────────────────────
# merge()
# ─────────────────────────────────────────────────────────────────────────────


class TestMerge:
    def test_skips_ticker_already_held(self, merge_env):
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("NVDA", "500"), ("ZZZ", "10")])
        result = merge_discoveries.merge()
        assert result["merged"] == ["ZZZ"]
        assert "NVDA" in result["skipped"]

    def test_skips_ticker_already_listed_as_candidate(self, merge_env):
        _seed_candidates(merge_env["db_path"], ["AAA"])
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("AAA", "10"), ("BBB", "20")])
        result = merge_discoveries.merge()
        assert result["merged"] == ["BBB"]
        assert "AAA" in result["skipped"]

    def test_skips_ticker_already_closed(self, merge_env):
        """2026-07-28: dedup now also covers closed positions -- a ticker
        that was already bought and sold shouldn't come right back as a
        fresh candidate either."""
        _seed_closed_position(merge_env["db_path"], "OLD")
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("OLD", "10"), ("NEW", "20")])
        result = merge_discoveries.merge()
        assert result["merged"] == ["NEW"]
        assert "OLD" in result["skipped"]

    def test_respects_max_size(self, merge_env):
        _seed_candidates(merge_env["db_path"], ["EXA", "EXB"])
        merge_env["params_path"].write_text(json.dumps({"watchlist": {"max_size": 3}}))
        make_discoveries_file(
            merge_env["discoveries_dir"], "2026-07-22",
            [("NEA", "10"), ("NEB", "20"), ("NEC", "30")],
        )
        result = merge_discoveries.merge()
        # 2 existing candidates + max_size 3 -> only 1 slot free
        assert result["merged"] == ["NEA"]
        assert any("NEB" in s for s in result["skipped"])
        assert any("NEC" in s for s in result["skipped"])
        assert any("max_size 3 reached" in s for s in result["skipped"])

    def test_dry_run_does_not_write(self, merge_env):
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("ZZZ", "10")])
        result = merge_discoveries.merge(dry_run=True)
        assert result["merged"] == ["ZZZ"]
        assert _candidates(merge_env["db_path"]) == {}

    def test_no_discoveries_file_found(self, merge_env):
        # discoveries dir exists but is empty
        result = merge_discoveries.merge()
        assert result["error"] == "no discoveries file found"
        assert result["merged"] == []

    def test_no_ticker_headers_found(self, merge_env):
        path = merge_env["discoveries_dir"] / "2026-07-22.md"
        path.write_text("# Probe Discovery — 2026-07-22\n\nNothing worth flagging today.\n")
        result = merge_discoveries.merge()
        assert result["error"] == "no ticker headers found in 2026-07-22.md"

    def test_picks_most_recent_dated_file_when_no_date_given(self, merge_env):
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-20", [("AAA", "10")])
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("BBB", "20")])
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-21", [("CCC", "30")])
        result = merge_discoveries.merge()
        assert result["source"] == "2026-07-22.md"
        assert result["merged"] == ["BBB"]

    def test_explicit_date_selects_that_file(self, merge_env):
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-20", [("AAA", "10")])
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("BBB", "20")])
        result = merge_discoveries.merge(date="2026-07-20")
        assert result["source"] == "2026-07-20.md"
        assert result["merged"] == ["AAA"]

    def test_missing_explicit_date_is_error(self, merge_env):
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("BBB", "20")])
        result = merge_discoveries.merge(date="2099-01-01")
        assert result["error"] == "no discoveries file found"

    def test_new_candidates_written_to_db(self, merge_env):
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("ZZZ", "10")])
        merge_discoveries.merge()
        candidates = _candidates(merge_env["db_path"])
        assert "ZZZ" in candidates
        assert candidates["ZZZ"]["source"] == "2026-07-22.md"
        assert candidates["ZZZ"]["idle_ticks"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# insert_into_watchlist — extracted 2026-07-27 so promote_candidates.py
# (discovery-pool -> watchlist bridge) reuses the exact same dedup/cap
# logic instead of reimplementing it. merge()'s own tests above already
# cover this indirectly; these exercise the helper directly with an
# arbitrary source_label instead of always a discoveries/*.md filename.
# ─────────────────────────────────────────────────────────────────────────────


class TestInsertIntoWatchlist:
    def test_skips_ticker_already_held(self, merge_env):
        result = merge_discoveries.insert_into_watchlist(
            ["NVDA", "ZZZ"], source_label="discovery_pool gen 1", db_path=merge_env["db_path"],
        )
        assert result["merged"] == ["ZZZ"]
        assert "NVDA" in result["skipped"]

    def test_respects_max_size(self, merge_env):
        _seed_candidates(merge_env["db_path"], ["EXA", "EXB"])
        merge_env["params_path"].write_text(json.dumps({"watchlist": {"max_size": 3}}))
        result = merge_discoveries.insert_into_watchlist(
            ["NEA", "NEB", "NEC"], source_label="discovery_pool gen 1", db_path=merge_env["db_path"],
        )
        assert result["merged"] == ["NEA"]
        assert any("max_size 3 reached" in s for s in result["skipped"])

    def test_dry_run_does_not_write(self, merge_env):
        result = merge_discoveries.insert_into_watchlist(
            ["ZZZ"], source_label="discovery_pool gen 1", dry_run=True, db_path=merge_env["db_path"],
        )
        assert result["merged"] == ["ZZZ"]
        assert _candidates(merge_env["db_path"]) == {}

    def test_uses_given_source_label(self, merge_env):
        merge_discoveries.insert_into_watchlist(
            ["ZZZ"], source_label="discovery_pool gen 7", db_path=merge_env["db_path"],
        )
        candidates = _candidates(merge_env["db_path"])
        assert candidates["ZZZ"]["source"] == "discovery_pool gen 7"

    def test_no_result_source_key_left_to_caller(self, merge_env):
        """Unlike merge(), this helper doesn't know about a discoveries
        file at all -- callers (merge() itself, promote_candidates.py) are
        responsible for adding their own 'source' key if they want one."""
        result = merge_discoveries.insert_into_watchlist(
            ["ZZZ"], source_label="anything", db_path=merge_env["db_path"],
        )
        assert "source" not in result

    def test_candidate_dicts_write_their_signals(self, merge_env):
        """2026-08-01: the pool already knows price/rsi/volume_ratio/
        macd_hist/sentiment/news_headline; this used to write ticker+source
        only and throw all of it away."""
        merge_discoveries.insert_into_watchlist(
            [{"ticker": "ZZZ", "price": 4.20, "rsi": 55.0, "volume_ratio": 6.1,
              "macd_hist": 0.03, "sentiment": -0.87, "news_headline": "ZZZ halted"}],
            source_label="discovery_pool gen 9", db_path=merge_env["db_path"],
        )
        row = _candidates(merge_env["db_path"])["ZZZ"]
        assert row["price"] == 4.20
        assert row["rsi"] == 55.0
        assert row["volume_ratio"] == 6.1
        assert row["macd_hist"] == 0.03
        assert row["sentiment"] == -0.87
        assert row["news_headline"] == "ZZZ halted"
        assert row["source"] == "discovery_pool gen 9"

    def test_candidate_dict_ignores_unknown_and_null_fields(self, merge_env):
        """Pool rows carry bookkeeping columns (in_band, screen_count, the
        new rank_score, ...) that aren't watchlist columns, and a partially
        screened row can have NULL signals -- neither should reach the
        upsert."""
        merge_discoveries.insert_into_watchlist(
            [{"ticker": "ZZZ", "price": 4.20, "rsi": None, "in_band": 1,
              "screen_count": 7, "rank_score": 1.4, "universe_generation": 3}],
            source_label="x", db_path=merge_env["db_path"],
        )
        row = _candidates(merge_env["db_path"])["ZZZ"]
        assert row["price"] == 4.20
        assert row["rsi"] is None
        assert row["volume_ratio"] is None

    def test_bare_ticker_strings_still_work(self, merge_env):
        """The discoveries/*.md path has no structured signals to pass."""
        result = merge_discoveries.insert_into_watchlist(
            ["ZZZ"], source_label="2026-08-01.md", db_path=merge_env["db_path"],
        )
        assert result["merged"] == ["ZZZ"]
        assert _candidates(merge_env["db_path"])["ZZZ"]["price"] is None

    def test_dict_candidates_respect_dedup_and_max_size(self, merge_env):
        merge_env["params_path"].write_text(json.dumps({"watchlist": {"max_size": 2}}))
        result = merge_discoveries.insert_into_watchlist(
            [{"ticker": "NVDA", "price": 1.0}, {"ticker": "AAA", "price": 2.0},
             {"ticker": "BBB", "price": 3.0}, {"ticker": "CCC", "price": 4.0}],
            source_label="discovery_pool gen 1", db_path=merge_env["db_path"],
        )
        assert result["merged"] == ["AAA", "BBB"]
        assert "NVDA" in result["skipped"]
        assert any("max_size 2 reached" in s for s in result["skipped"])

    def test_default_db_path_uses_trader_db_default(self, merge_env):
        """No db_path passed -- should fall back to trader_db.DB_PATH
        (already monkeypatched by the fixture), same as every other
        migrated script's convention."""
        result = merge_discoveries.insert_into_watchlist(["ZZZ"], source_label="x")
        assert result["merged"] == ["ZZZ"]
        assert "ZZZ" in _candidates(merge_env["db_path"])
