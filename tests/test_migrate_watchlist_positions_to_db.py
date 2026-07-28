#!/usr/bin/env python3
"""
Unit tests for scripts/migrate_watchlist_positions_to_db.py's one real
piece of generalizable logic: read_open_positions()'s live parse of
positions/*.md's "**Entry**: $X avg | N shares" line. The CANDIDATES/
CLOSED_POSITIONS lists are hand-transcribed historical data, not logic --
nothing to unit test there (this was a one-time migration, already run
2026-07-28; positions/*.md no longer exists in the live repo).
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import migrate_watchlist_positions_to_db as migrate  # noqa: E402


class TestReadOpenPositions:
    def test_parses_ticker_shares_entry_price(self, tmp_path, monkeypatch):
        monkeypatch.setattr(migrate, "POSITIONS_DIR", tmp_path)
        (tmp_path / "AAA.md").write_text(
            "# AAA Position Thesis\n\n- **Entry**: $30.98 avg | 1 shares\n- **Current**: $31.91\n"
        )
        rows = migrate.read_open_positions()
        assert len(rows) == 1
        assert rows[0]["ticker"] == "AAA"
        assert rows[0]["shares"] == 1.0
        assert rows[0]["entry_price"] == 30.98

    def test_multiple_shares_parsed_correctly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(migrate, "POSITIONS_DIR", tmp_path)
        (tmp_path / "BOX.md").write_text("- **Entry**: $30.34 avg | 5 shares\n")
        rows = migrate.read_open_positions()
        assert rows[0]["shares"] == 5.0

    def test_unparseable_file_skipped_with_warning_not_crash(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(migrate, "POSITIONS_DIR", tmp_path)
        (tmp_path / "BAD.md").write_text("no entry line here at all\n")
        rows = migrate.read_open_positions()
        assert rows == []
        assert "could not parse" in capsys.readouterr().err

    def test_ticker_with_no_hand_transcribed_context_still_included(self, tmp_path, monkeypatch, capsys):
        """A ticker not in POSITION_CONTEXT (e.g. a real future position
        this migration was never updated for) should still produce a row
        -- entry_time falls back to 'unknown' rather than crashing, with a
        warning so it's visible, not silently wrong."""
        monkeypatch.setattr(migrate, "POSITIONS_DIR", tmp_path)
        (tmp_path / "ZZZ.md").write_text("- **Entry**: $5.00 avg | 2 shares\n")
        rows = migrate.read_open_positions()
        assert len(rows) == 1
        assert rows[0]["entry_time"] == "unknown"
        assert "no hand-transcribed context" in capsys.readouterr().err
