#!/usr/bin/env python3
"""
Unit tests for scripts/workspace_review.py — mechanized consistency checks
between strategy.md/params.json/executor.py. Pure logic, tmp_path-based
synthetic fixtures, no network.
"""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import workspace_review  # noqa: E402

VALID_PARAMS = {
    "agent": "stonks",
    "risk": {"stop_loss_pct": -10.0},
    "guardrail_gates": {"cash": True, "_note": "x"},
    "universe": {"min_price": 1.0},
    "watchlist": {"path": "strategies/watchlist.md"},
}

VALID_STRATEGY = "# Stonks — Strategy stonks.strat:v1.4\n\n## Current Approach\n\n- some rule\n"

MINIMAL_EXECUTOR = '''
GATES = {
    "cash": gate_cash,
}
STOP = params["risk"]["stop_loss_pct"]
MIN_PRICE = params["universe"]["min_price"]
WATCHLIST_PATH = params["watchlist"]["path"]
'''


class TestCheckParamsJsonValid:
    def test_valid_json_with_required_keys_no_findings(self):
        findings, params = workspace_review.check_params_json_valid(json.dumps(VALID_PARAMS))
        assert findings == []
        assert params == VALID_PARAMS

    def test_malformed_json_is_critical(self):
        findings, params = workspace_review.check_params_json_valid("{not valid json")
        assert len(findings) == 1
        assert findings[0][0] == "critical"
        assert params == {}

    def test_missing_required_key_is_critical(self):
        broken = {k: v for k, v in VALID_PARAMS.items() if k != "guardrail_gates"}
        findings, params = workspace_review.check_params_json_valid(json.dumps(broken))
        assert any(sev == "critical" and "guardrail_gates" in msg for sev, msg in findings)


class TestCheckStrategyMdStructure:
    def test_valid_strategy_no_findings(self):
        assert workspace_review.check_strategy_md_structure(VALID_STRATEGY) == []

    def test_empty_text_is_critical(self):
        findings = workspace_review.check_strategy_md_structure("")
        assert findings and findings[0][0] == "critical"

    def test_missing_version_header_is_critical(self):
        findings = workspace_review.check_strategy_md_structure("# Just a title\n\nsome text")
        assert any(sev == "critical" for sev, msg in findings)

    def test_no_version_history_required(self):
        """2026-07-23: git tracks version rationale now, not this file —
        a valid version header with no Version History section is clean."""
        text = "# Stonks — Strategy stonks.strat:v1.4\n\nno history section, and that's fine"
        assert workspace_review.check_strategy_md_structure(text) == []


class TestCheckVersionSync:
    def test_matching_versions_no_findings(self):
        params = {"strategy_version": "stonks.strat:v1.4"}
        assert workspace_review.check_version_sync(params, VALID_STRATEGY) == []

    def test_mismatched_versions_is_warning(self):
        params = {"strategy_version": "stonks.strat:v1.2.0"}
        findings = workspace_review.check_version_sync(params, VALID_STRATEGY)
        assert len(findings) == 1
        assert findings[0][0] == "warning"
        assert "v1.2.0" in findings[0][1] and "v1.4" in findings[0][1]

    def test_empty_params_or_strategy_no_findings(self):
        assert workspace_review.check_version_sync({}, VALID_STRATEGY) == []
        assert workspace_review.check_version_sync({"strategy_version": "x"}, "") == []


class TestCheckGuardrailGatesDrift:
    def test_matching_gates_no_findings(self):
        params = {"guardrail_gates": {"cash": True}}
        assert workspace_review.check_guardrail_gates_drift(params, MINIMAL_EXECUTOR) == []

    def test_declared_but_unreferenced_gate_is_warning(self):
        params = {"guardrail_gates": {"cash": True, "ghost_gate": True}}
        findings = workspace_review.check_guardrail_gates_drift(params, MINIMAL_EXECUTOR)
        assert any("ghost_gate" in msg and "dead toggle" in msg for sev, msg in findings)

    def test_referenced_but_undeclared_gate_is_warning(self):
        executor_text = MINIMAL_EXECUTOR + '\ntoggles.get("hard_stop", True)\n'
        params = {"guardrail_gates": {"cash": True}}
        findings = workspace_review.check_guardrail_gates_drift(params, executor_text)
        assert any("hard_stop" in msg and "undocumented gate" in msg for sev, msg in findings)

    def test_toggle_get_calls_recognized_not_just_gates_dict(self):
        executor_text = MINIMAL_EXECUTOR + '\ntoggles.get("position_size_trim", True)\n'
        params = {"guardrail_gates": {"cash": True, "position_size_trim": True}}
        assert workspace_review.check_guardrail_gates_drift(params, executor_text) == []

    def test_underscore_prefixed_keys_ignored(self):
        params = {"guardrail_gates": {"cash": True, "_note": "explanatory text"}}
        assert workspace_review.check_guardrail_gates_drift(params, MINIMAL_EXECUTOR) == []


class TestCheckDeadParams:
    def test_referenced_key_no_finding(self, tmp_path, monkeypatch):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "executor.py").write_text('x = params["risk"]["stop_loss_pct"]')
        monkeypatch.setattr(workspace_review, "SCRIPTS_DIR", scripts_dir)
        monkeypatch.setattr(workspace_review, "REPO_ROOT", tmp_path)
        params = {"risk": {"stop_loss_pct": -10.0}}
        assert workspace_review.check_dead_params(params) == []

    def test_unreferenced_key_is_warning(self, tmp_path, monkeypatch):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "executor.py").write_text("# nothing relevant here")
        monkeypatch.setattr(workspace_review, "SCRIPTS_DIR", scripts_dir)
        monkeypatch.setattr(workspace_review, "REPO_ROOT", tmp_path)
        params = {"risk": {"totally_unused_param": 1.0}}
        findings = workspace_review.check_dead_params(params)
        assert any("totally_unused_param" in msg for sev, msg in findings)

    def test_key_referenced_only_in_live_doc_not_flagged(self, tmp_path, monkeypatch):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "executor.py").write_text("# nothing relevant")
        (tmp_path / "strategy.md").write_text("Follow idle_ticks_before_drop from params.")
        (tmp_path / "skills").mkdir()
        (tmp_path / "strategies").mkdir()
        monkeypatch.setattr(workspace_review, "SCRIPTS_DIR", scripts_dir)
        monkeypatch.setattr(workspace_review, "REPO_ROOT", tmp_path)
        params = {"watchlist": {"idle_ticks_before_drop": 24}}
        assert workspace_review.check_dead_params(params) == []

    def test_design_doc_and_readme_excluded_from_live_docs(self, tmp_path, monkeypatch):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "executor.py").write_text("# nothing relevant")
        (tmp_path / "v4-spec.md").write_text("some_future_param is planned for Phase 9")
        (tmp_path / "README.md").write_text("some_future_param appears here too")
        (tmp_path / "skills").mkdir()
        (tmp_path / "strategies").mkdir()
        monkeypatch.setattr(workspace_review, "SCRIPTS_DIR", scripts_dir)
        monkeypatch.setattr(workspace_review, "REPO_ROOT", tmp_path)
        params = {"risk": {"some_future_param": 1.0}}
        findings = workspace_review.check_dead_params(params)
        assert any("some_future_param" in msg for sev, msg in findings)

    def test_underscore_prefixed_keys_and_non_dict_blocks_skipped(self, tmp_path, monkeypatch):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "executor.py").write_text("# nothing")
        monkeypatch.setattr(workspace_review, "SCRIPTS_DIR", scripts_dir)
        monkeypatch.setattr(workspace_review, "REPO_ROOT", tmp_path)
        params = {"risk": {"_source": "explanatory, not a real param"}, "universe": "not-a-dict"}
        assert workspace_review.check_dead_params(params) == []

    def test_key_tracked_by_proposal_is_tracked_not_warning(self, tmp_path, monkeypatch):
        """2026-07-23: a dead param already written up as an evolution
        proposal is genuinely unresolved but through the right channel —
        should report as 'tracked', not fail CI as an unaddressed warning
        forever just because implementing it was deferred responsibly."""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "executor.py").write_text("# nothing relevant")
        proposals_dir = tmp_path / "proposals"
        proposals_dir.mkdir()
        (proposals_dir / "2026-07-23-x.md").write_text(
            "# Proposal: Implement max_portfolio_risk_pct gate\n\nmax_portfolio_risk_pct needs design work.\n")
        monkeypatch.setattr(workspace_review, "SCRIPTS_DIR", scripts_dir)
        monkeypatch.setattr(workspace_review, "REPO_ROOT", tmp_path)
        params = {"risk": {"max_portfolio_risk_pct": 8.0}}
        findings = workspace_review.check_dead_params(params)
        assert len(findings) == 1
        assert findings[0][0] == "tracked"
        assert "max_portfolio_risk_pct" in findings[0][1]

    def test_untracked_dead_param_still_a_warning_with_proposals_dir_present(self, tmp_path, monkeypatch):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "executor.py").write_text("# nothing relevant")
        proposals_dir = tmp_path / "proposals"
        proposals_dir.mkdir()
        (proposals_dir / "2026-07-23-x.md").write_text("# Proposal: unrelated\n\nsomething else entirely.\n")
        monkeypatch.setattr(workspace_review, "SCRIPTS_DIR", scripts_dir)
        monkeypatch.setattr(workspace_review, "REPO_ROOT", tmp_path)
        params = {"risk": {"totally_unused_param": 1.0}}
        findings = workspace_review.check_dead_params(params)
        assert findings == [("warning", "params.json.risk.totally_unused_param isn't referenced in "
                                         "scripts/*.py or any live-consumed doc — possibly dead")]

    def test_missing_proposals_dir_does_not_crash(self, tmp_path, monkeypatch):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "executor.py").write_text("# nothing")
        monkeypatch.setattr(workspace_review, "SCRIPTS_DIR", scripts_dir)
        monkeypatch.setattr(workspace_review, "REPO_ROOT", tmp_path)
        params = {"risk": {"totally_unused_param": 1.0}}
        findings = workspace_review.check_dead_params(params)
        assert findings[0][0] == "warning"


class TestRunAllChecksAndExitCodes:
    def _write_workspace(self, tmp_path, params=VALID_PARAMS, strategy=VALID_STRATEGY, executor=MINIMAL_EXECUTOR):
        (tmp_path / "params.json").write_text(json.dumps(params))
        (tmp_path / "strategy.md").write_text(strategy)
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "executor.py").write_text(executor)
        (tmp_path / "skills").mkdir()
        (tmp_path / "strategies").mkdir()
        return scripts_dir

    def _patch_paths(self, tmp_path, monkeypatch, scripts_dir):
        monkeypatch.setattr(workspace_review, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(workspace_review, "PARAMS_PATH", tmp_path / "params.json")
        monkeypatch.setattr(workspace_review, "STRATEGY_PATH", tmp_path / "strategy.md")
        monkeypatch.setattr(workspace_review, "EXECUTOR_PATH", scripts_dir / "executor.py")
        monkeypatch.setattr(workspace_review, "SCRIPTS_DIR", scripts_dir)
        monkeypatch.setattr(workspace_review, "STATE_DIR", tmp_path / "state")
        monkeypatch.setattr(workspace_review, "SENTINEL_PATH", tmp_path / "state" / ".workspace_blocked")

    def test_clean_workspace_reports_ok(self, tmp_path, monkeypatch):
        scripts_dir = self._write_workspace(tmp_path)
        self._patch_paths(tmp_path, monkeypatch, scripts_dir)
        report = workspace_review.run_all_checks()
        assert report["ok"] is True
        assert report["critical"] == []
        assert report["warnings"] == []

    def test_broken_workspace_reports_critical(self, tmp_path, monkeypatch):
        scripts_dir = self._write_workspace(tmp_path, params={"agent": "stonks"})  # missing required keys
        self._patch_paths(tmp_path, monkeypatch, scripts_dir)
        report = workspace_review.run_all_checks()
        assert report["ok"] is False
        assert len(report["critical"]) > 0


class TestGateSentinel:
    def _patch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(workspace_review, "STATE_DIR", tmp_path / "state")
        monkeypatch.setattr(workspace_review, "SENTINEL_PATH", tmp_path / "state" / ".workspace_blocked")

    def test_status_clear_when_no_sentinel(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch)
        assert workspace_review.gate_status().startswith("CLEAR")

    def test_block_then_status_blocked(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch)
        workspace_review.block_gate("params.json broken")
        assert "BLOCKED" in workspace_review.gate_status()
        assert "params.json broken" in workspace_review.gate_status()

    def test_clear_removes_sentinel(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch)
        workspace_review.block_gate("reason")
        workspace_review.clear_gate()
        assert workspace_review.gate_status().startswith("CLEAR")

    def test_clear_when_nothing_blocked_is_a_no_op(self, tmp_path, monkeypatch):
        self._patch(tmp_path, monkeypatch)
        workspace_review.clear_gate()  # should not raise
        assert workspace_review.gate_status().startswith("CLEAR")
