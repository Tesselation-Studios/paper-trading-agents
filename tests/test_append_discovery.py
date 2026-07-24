#!/usr/bin/env python3
"""Unit tests for scripts/append_discovery.py — pure logic, no network.
discovery_scan.get_universe_price_band / write_discoveries_file are
monkeypatched, matching this repo's existing no-network test convention."""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import append_discovery  # noqa: E402
import discovery_scan  # noqa: E402
import merge_discoveries  # noqa: E402


class TestAppendDiscoveryMain:
    def test_writes_candidate_within_universe_band(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        out_path = tmp_path / "2026-07-24.md"
        monkeypatch.setattr(sys, "argv", [
            "append_discovery.py", "--ticker", "xyz", "--price", "12.34",
            "--note", "Reuters: XYZ wins DoD contract", "--source", "freeform",
        ])

        real_write = discovery_scan.write_discoveries_file

        def fake_write(candidates, min_price, max_price, path=None):
            return real_write(candidates, min_price, max_price, path=out_path)

        monkeypatch.setattr(discovery_scan, "write_discoveries_file", fake_write)

        rc = append_discovery.main()
        assert rc == 0

        captured = json.loads(capsys.readouterr().out)
        assert captured["written"] == "XYZ"  # uppercased

        text = out_path.read_text()
        assert "## XYZ — $12.34" in text
        assert "DoD contract" in text
        assert "Source: freeform" in text
        assert merge_discoveries.extract_candidates(text) == ["XYZ"]

    def test_price_outside_universe_band_rejected(self, monkeypatch, capsys):
        monkeypatch.setattr(discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(sys, "argv", [
            "append_discovery.py", "--ticker", "ABC", "--price", "999.00",
            "--note", "way too expensive",
        ])
        rc = append_discovery.main()
        assert rc == 1
        captured = json.loads(capsys.readouterr().out)
        assert captured["written"] is None
        assert "outside" in captured["error"]
