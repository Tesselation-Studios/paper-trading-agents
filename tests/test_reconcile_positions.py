#!/usr/bin/env python3
"""
Tests for scripts/reconcile_positions.py (2026-08-03) -- the daily
integrity check between live Alpaca positions and trader_db's positions
table (root cause of the STVN/KEX/DXCM phantom-position incidents,
tasks/pending.md). Same conventions as test_executor_audit.py: real
sqlite3 under tmp_path, no DB-logic mocking, only Alpaca HTTP faked via
urllib.request.urlopen.
"""
import json
import sys
import urllib.request
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import executor  # noqa: E402
import reconcile_positions  # noqa: E402
import trader_db  # noqa: E402
import workspace_review  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
    monkeypatch.setattr(reconcile_positions, "STATUS_PATH", tmp_path / "reconciliation_status.json")
    monkeypatch.setattr(workspace_review, "STATE_DIR", tmp_path)
    monkeypatch.setenv("ALPACA_STONKS_KEY", "test-key")
    monkeypatch.setenv("ALPACA_STONKS_SECRET", "test-secret")


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(positions_response):
    def fake(req):
        if req.full_url.endswith("/v2/positions"):
            return _FakeResponse(positions_response)
        return _FakeResponse({})
    return fake


def _seed_db_position(ticker, shares=5.0):
    conn = trader_db.get_conn()
    try:
        trader_db.upsert_position(conn, ticker=ticker, shares=shares, entry_price=10.0, entry_time="2026-08-01T10:00:00Z")
    finally:
        conn.close()


def _alpaca_position(symbol, qty):
    return {"symbol": symbol, "qty": str(qty), "avg_entry_price": "10.00", "market_value": "50.00"}


class TestCleanReconciliation:
    def test_no_mismatch_when_alpaca_and_db_agree(self, monkeypatch):
        _seed_db_position("AAA", shares=5.0)
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([_alpaca_position("AAA", 5)]))
        result = reconcile_positions.reconcile("stonks")
        assert result["critical"] == []
        assert result["warnings"] == []

    def test_no_positions_anywhere_is_clean(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([]))
        result = reconcile_positions.reconcile("stonks")
        assert result["critical"] == []
        assert result["warnings"] == []


class TestPhantomAlpacaPosition:
    def test_alpaca_position_not_in_db_is_critical(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([_alpaca_position("ZZZ", 3)]))
        result = reconcile_positions.reconcile("stonks")
        assert len(result["critical"]) == 1
        assert "phantom Alpaca position" in result["critical"][0]
        assert "ZZZ" in result["critical"][0]
        assert result["warnings"] == []


class TestPhantomDbPosition:
    def test_db_open_position_not_in_alpaca_is_critical(self, monkeypatch):
        _seed_db_position("KEX", shares=2.0)
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([]))
        result = reconcile_positions.reconcile("stonks")
        assert len(result["critical"]) == 1
        assert "phantom DB position" in result["critical"][0]
        assert "KEX" in result["critical"][0]

    def test_closed_db_position_not_flagged(self, monkeypatch):
        """A closed position correctly absent from Alpaca must not be
        flagged -- only OPEN db positions are reconciled."""
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.close_position(conn, ticker="AAA", closed_at="t2", close_reason="exit",
                                  realized_pnl=1.0, realized_return_pct=1.0)
        conn.close()
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([]))
        result = reconcile_positions.reconcile("stonks")
        assert result["critical"] == []


class TestQuantityMismatch:
    def test_stvn_pattern_2sh_vs_3sh_is_warning_not_critical(self, monkeypatch):
        _seed_db_position("STVN", shares=2.0)
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([_alpaca_position("STVN", 3)]))
        result = reconcile_positions.reconcile("stonks")
        assert result["critical"] == []
        assert len(result["warnings"]) == 1
        assert "quantity mismatch" in result["warnings"][0]
        assert "STVN" in result["warnings"][0]

    def test_tiny_fractional_difference_within_tolerance_not_flagged(self, monkeypatch):
        _seed_db_position("AAA", shares=5.0)
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([_alpaca_position("AAA", 5.001)]))
        result = reconcile_positions.reconcile("stonks")
        assert result["critical"] == []
        assert result["warnings"] == []


class TestMultipleMismatchesAtOnce:
    def test_reports_all_findings_together(self, monkeypatch):
        _seed_db_position("KEX", shares=2.0)   # phantom DB
        _seed_db_position("STVN", shares=2.0)  # quantity mismatch
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([
            _alpaca_position("STVN", 3),
            _alpaca_position("ZZZ", 1),  # phantom Alpaca
        ]))
        result = reconcile_positions.reconcile("stonks")
        assert len(result["critical"]) == 2
        assert len(result["warnings"]) == 1


class TestStatusFileAndExitCode:
    def test_write_status_persists_result(self, tmp_path, monkeypatch):
        reconcile_positions.STATUS_PATH = tmp_path / "status.json"
        result = {"checked_at": "2026-08-03T00:00:00Z", "critical": ["x"], "warnings": []}
        reconcile_positions.write_status(result)
        loaded = json.loads(reconcile_positions.STATUS_PATH.read_text())
        assert loaded == result

    def test_main_returns_nonzero_on_critical_finding(self, monkeypatch, capsys):
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([_alpaca_position("ZZZ", 1)]))
        monkeypatch.setattr(sys, "argv", ["reconcile_positions.py"])
        assert reconcile_positions.main() == 1

    def test_main_returns_zero_when_clean(self, monkeypatch, capsys):
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([]))
        monkeypatch.setattr(sys, "argv", ["reconcile_positions.py"])
        assert reconcile_positions.main() == 0


class TestWorkspaceReviewIntegration:
    """check_position_reconciliation() (workspace_review.py) reads the
    status file this script writes -- confirms the two actually wire
    together, not just each independently correct."""

    def test_critical_finding_surfaces_in_workspace_review(self, tmp_path, monkeypatch):
        status_path = tmp_path / "reconciliation_status.json"
        monkeypatch.setattr(workspace_review, "STATE_DIR", tmp_path)
        status_path.write_text(json.dumps({
            "checked_at": "2026-08-03T00:00:00Z",
            "critical": ["phantom Alpaca position: ZZZ (1 shares) ..."],
            "warnings": [],
        }))
        findings = workspace_review.check_position_reconciliation()
        assert len(findings) == 1
        severity, msg = findings[0]
        assert severity == "critical"
        assert "position reconciliation" in msg
        assert "ZZZ" in msg

    def test_missing_status_file_yields_no_findings(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workspace_review, "STATE_DIR", tmp_path)
        assert workspace_review.check_position_reconciliation() == []

    def test_corrupt_status_file_yields_no_findings_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workspace_review, "STATE_DIR", tmp_path)
        (tmp_path / "reconciliation_status.json").write_text("not json")
        assert workspace_review.check_position_reconciliation() == []

    def test_end_to_end_gate_blocks_on_phantom_position(self, tmp_path, monkeypatch):
        """The actual production flow: reconcile_positions.py writes the
        status file, then a later workspace_review.py --gate call must
        set state/.workspace_blocked from it."""
        monkeypatch.setattr(reconcile_positions, "STATUS_PATH", tmp_path / "reconciliation_status.json")
        monkeypatch.setattr(workspace_review, "STATE_DIR", tmp_path)
        monkeypatch.setattr(workspace_review, "SENTINEL_PATH", tmp_path / ".workspace_blocked")
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([_alpaca_position("ZZZ", 1)]))

        result = reconcile_positions.reconcile("stonks")
        reconcile_positions.write_status(result)

        report = workspace_review.run_all_checks()
        assert not report["ok"]
        assert any("phantom Alpaca position" in c for c in report["critical"])

        workspace_review.block_gate("; ".join(report["critical"]))
        assert "BLOCKED" in workspace_review.gate_status()

    def test_clean_reconciliation_does_not_block(self, tmp_path, monkeypatch):
        monkeypatch.setattr(reconcile_positions, "STATUS_PATH", tmp_path / "reconciliation_status.json")
        monkeypatch.setattr(workspace_review, "STATE_DIR", tmp_path)
        monkeypatch.setattr(workspace_review, "SENTINEL_PATH", tmp_path / ".workspace_blocked")
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen([]))

        result = reconcile_positions.reconcile("stonks")
        reconcile_positions.write_status(result)

        findings = workspace_review.check_position_reconciliation()
        assert findings == []
