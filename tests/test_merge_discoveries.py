#!/usr/bin/env python3
"""
Unit tests for scripts/merge_discoveries.py — mechanical feed of
discoveries/YYYY-MM-DD.md ticker candidates into strategies/watchlist.md.

DISCOVERIES_DIR, WATCHLIST_PATH, and PARAMS_PATH are monkeypatched to
tmp_path fixtures in every test that touches the filesystem, so tests never
read or write the real discoveries/ dir, watchlist.md, or params.json.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import merge_discoveries  # noqa: E402


DEFAULT_WATCHLIST = """# Watchlist — Growing/Shrinking Candidate List

Format: `TICKER — idle_ticks: N — note`

## Currently Held (always on the list, idle_ticks doesn't apply while open)
- NVDA — open position

## Candidates
"""


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
    """Isolated discoveries/, watchlist.md, and params.json under tmp_path."""
    discoveries_dir = tmp_path / "discoveries"
    discoveries_dir.mkdir()
    watchlist_path = tmp_path / "strategies" / "watchlist.md"
    watchlist_path.parent.mkdir()
    watchlist_path.write_text(DEFAULT_WATCHLIST)
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps({"watchlist": {"max_size": 30}}))

    monkeypatch.setattr(merge_discoveries, "DISCOVERIES_DIR", discoveries_dir)
    monkeypatch.setattr(merge_discoveries, "WATCHLIST_PATH", watchlist_path)
    monkeypatch.setattr(merge_discoveries, "PARAMS_PATH", params_path)

    return {
        "discoveries_dir": discoveries_dir,
        "watchlist_path": watchlist_path,
        "params_path": params_path,
    }


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
        merge_env["watchlist_path"].write_text(
            DEFAULT_WATCHLIST + "- AAA — idle_ticks: 0 — from prior\n"
        )
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("AAA", "10"), ("BBB", "20")])
        result = merge_discoveries.merge()
        assert result["merged"] == ["BBB"]
        assert "AAA" in result["skipped"]

    def test_respects_max_size(self, merge_env):
        merge_env["watchlist_path"].write_text(
            DEFAULT_WATCHLIST
            + "- EXA — idle_ticks: 0 — from prior\n"
            + "- EXB — idle_ticks: 0 — from prior\n"
        )
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

    def test_dry_run_does_not_write_file(self, merge_env):
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("ZZZ", "10")])
        before = merge_env["watchlist_path"].read_text()
        result = merge_discoveries.merge(dry_run=True)
        after = merge_env["watchlist_path"].read_text()
        assert result["merged"] == ["ZZZ"]
        assert before == after

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

    def test_new_candidates_written_into_candidates_section(self, merge_env):
        make_discoveries_file(merge_env["discoveries_dir"], "2026-07-22", [("ZZZ", "10")])
        merge_discoveries.merge()
        text = merge_env["watchlist_path"].read_text()
        assert "- ZZZ — idle_ticks: 0 — from 2026-07-22.md" in text
