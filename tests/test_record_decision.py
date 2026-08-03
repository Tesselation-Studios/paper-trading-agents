#!/usr/bin/env python3
"""Unit tests for scripts/record_decision.py's new reconcile subcommand and
the hardened decision --conviction default (2026-07-27). decisions.py's DB
writes are mocked -- this only tests the CLI's own dispatch/self-compute
logic, not decisions.py itself (which has no test coverage of its own,
pre-existing, out of scope here)."""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import record_decision  # noqa: E402
import signals  # noqa: E402
import trader_db  # noqa: E402


class TestReconcileSubcommand:
    def test_reconcile_prints_combined_confidence(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", [
            "record_decision.py", "reconcile",
            "--features", json.dumps({"technical": {"direction": "bullish", "confidence": 0.8}}),
        ])
        record_decision.main()
        out = json.loads(capsys.readouterr().out)
        assert out["recommendation"] == "bullish"
        assert out["combined_confidence"] > 0

    def test_reconcile_invalid_json_errors_without_crashing(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["record_decision.py", "reconcile", "--features", "not json"])
        with pytest.raises(SystemExit):
            record_decision.main()
        out = json.loads(capsys.readouterr().out)
        assert "error" in out

    def test_reconcile_writes_nothing_to_decisions(self, monkeypatch, capsys):
        called = []
        monkeypatch.setattr(record_decision.decisions, "record_decision", lambda **kw: called.append(kw))
        monkeypatch.setattr(sys, "argv", ["record_decision.py", "reconcile", "--features", "{}"])
        record_decision.main()
        assert called == []


class TestDecisionConvictionDefault:
    def _mock_decisions(self, monkeypatch):
        captured = {}

        def fake_record_decision(**kwargs):
            captured.update(kwargs)
            return {"ok": True}
        monkeypatch.setattr(record_decision.decisions, "record_decision", fake_record_decision)
        return captured

    def test_omitted_conviction_self_computes_from_features(self, monkeypatch, capsys):
        captured = self._mock_decisions(monkeypatch)
        monkeypatch.setattr(sys, "argv", [
            "record_decision.py", "decision", "--ticker", "AAA", "--action", "BUY",
            "--features", json.dumps({"technical": {"direction": "bullish", "confidence": 0.8}}),
        ])
        record_decision.main()
        expected = signals.reconcile_signals(
            {"technical": {"direction": "bullish", "confidence": 0.8}})["combined_confidence"]
        assert captured["conviction"] == pytest.approx(expected)
        assert captured["conviction"] != 0.0  # the old silent-default behavior

    def test_omitted_conviction_with_no_features_is_zero_not_crash(self, monkeypatch):
        captured = self._mock_decisions(monkeypatch)
        monkeypatch.setattr(sys, "argv", [
            "record_decision.py", "decision", "--ticker", "AAA", "--action", "HOLD",
        ])
        record_decision.main()
        assert captured["conviction"] == 0.0

    def test_explicit_conviction_overrides_self_compute(self, monkeypatch):
        captured = self._mock_decisions(monkeypatch)
        monkeypatch.setattr(sys, "argv", [
            "record_decision.py", "decision", "--ticker", "AAA", "--action", "BUY",
            "--conviction", "0.9",
            "--features", json.dumps({"technical": {"direction": "bearish", "confidence": 0.8}}),
        ])
        record_decision.main()
        assert captured["conviction"] == 0.9

    def test_reconciled_echoed_back_in_result(self, monkeypatch, capsys):
        self._mock_decisions(monkeypatch)
        monkeypatch.setattr(sys, "argv", [
            "record_decision.py", "decision", "--ticker", "AAA", "--action", "BUY",
            "--conviction", "0.6",
        ])
        record_decision.main()
        out = json.loads(capsys.readouterr().out)
        assert "reconciled" in out
        assert "combined_confidence" in out["reconciled"]


class TestDecisionSubcommandStandaloneStillWorks:
    """2026-08-02: executor.py now also writes decisions rows directly on
    every real BUY/SELL fill (mechanized) -- this doesn't remove the
    standalone CLI path, only makes it optional for the tick loop. Real
    tmp_path DB, not mocked (matching test_trader_query.py's un-mocked
    convention), to guard against this path silently breaking now that
    it's no longer the only writer."""

    def test_decision_subcommand_still_writes_real_decisions_row_standalone(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        monkeypatch.setattr(sys, "argv", [
            "record_decision.py", "decision", "--ticker", "AAA", "--action", "BUY",
            "--conviction", "0.6", "--rationale", "standalone backfill entry",
        ])
        record_decision.main()

        conn = trader_db.get_conn()
        try:
            row = conn.execute("SELECT * FROM decisions WHERE ticker = 'AAA'").fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["decision"] == "BUY"
        assert row["rationale"] == "standalone backfill entry"
