#!/usr/bin/env python3
"""
Unit tests for scripts/evolution_proposal.py — tier classification and
proposal file lifecycle (create/list/resolve). No network.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pytest  # noqa: E402
import evolution_proposal  # noqa: E402


class TestClassifyTier:
    def test_strategy_only_is_auto(self):
        assert evolution_proposal.classify_tier(["strategy.md"]) == "auto"

    def test_params_only_is_auto(self):
        assert evolution_proposal.classify_tier(["params.json"]) == "auto"

    def test_strategy_and_params_together_is_auto(self):
        assert evolution_proposal.classify_tier(["strategy.md", "params.json"]) == "auto"

    def test_touching_executor_is_review_required(self):
        assert evolution_proposal.classify_tier(["scripts/executor.py"]) == "review_required"

    def test_mixing_strategy_with_code_is_review_required(self):
        assert evolution_proposal.classify_tier(["strategy.md", "scripts/executor.py"]) == "review_required"

    def test_tools_md_is_review_required(self):
        assert evolution_proposal.classify_tier(["TOOLS.md"]) == "review_required"

    def test_openclaw_json_is_forbidden_not_classified(self):
        with pytest.raises(evolution_proposal.ForbiddenProposalTarget):
            evolution_proposal.classify_tier(["openclaw.json"])

    def test_openclaw_json_forbidden_even_mixed_with_allowed_files(self):
        with pytest.raises(evolution_proposal.ForbiddenProposalTarget):
            evolution_proposal.classify_tier(["strategy.md", "openclaw.json"])

    def test_nested_path_to_openclaw_json_still_forbidden(self):
        with pytest.raises(evolution_proposal.ForbiddenProposalTarget):
            evolution_proposal.classify_tier(["/home/openclaw/.openclaw/openclaw.json"])


class TestSlugify:
    def test_basic_title(self):
        assert evolution_proposal.slugify("Widen stop loss to -12%") == "widen-stop-loss-to-12"

    def test_empty_title_falls_back(self):
        assert evolution_proposal.slugify("   ") == "proposal"


class TestWriteProposal:
    def test_creates_file_with_expected_fields(self, tmp_path):
        path = evolution_proposal.write_proposal(
            title="Widen profit target",
            rationale="v1.1 backtest shows better Sharpe",
            files_changed=["strategy.md", "params.json"],
            evidence="replay_check.py --split-window: Sharpe +0.9/+0.8 both halves",
            proposals_dir=tmp_path,
        )
        assert path.exists()
        text = path.read_text()
        assert "**Tier**: auto" in text
        assert "**Status**: open" in text
        assert "Widen profit target" in text
        assert "strategy.md, params.json" in text

    def test_review_required_tier_written_for_code_change(self, tmp_path):
        path = evolution_proposal.write_proposal(
            title="Add earnings-day gate",
            rationale="repeated losses around earnings",
            files_changed=["scripts/executor.py"],
            evidence="5 journal entries flagged this pattern",
            proposals_dir=tmp_path,
        )
        assert "**Tier**: review_required" in path.read_text()

    def test_forbidden_target_raises_and_writes_nothing(self, tmp_path):
        with pytest.raises(evolution_proposal.ForbiddenProposalTarget):
            evolution_proposal.write_proposal(
                title="Change model", rationale="x", files_changed=["openclaw.json"],
                evidence="x", proposals_dir=tmp_path,
            )
        assert list(tmp_path.glob("*.md")) == []

    def test_duplicate_title_same_day_does_not_clobber(self, tmp_path):
        p1 = evolution_proposal.write_proposal(
            title="Same Title", rationale="a", files_changed=["strategy.md"],
            evidence="a", proposals_dir=tmp_path,
        )
        p2 = evolution_proposal.write_proposal(
            title="Same Title", rationale="b", files_changed=["strategy.md"],
            evidence="b", proposals_dir=tmp_path,
        )
        assert p1 != p2
        assert p1.exists() and p2.exists()


class TestListAndResolveProposals:
    def test_list_all_and_filter_by_status(self, tmp_path):
        evolution_proposal.write_proposal(
            title="One", rationale="a", files_changed=["strategy.md"], evidence="a", proposals_dir=tmp_path)
        p2 = evolution_proposal.write_proposal(
            title="Two", rationale="b", files_changed=["scripts/executor.py"], evidence="b", proposals_dir=tmp_path)
        evolution_proposal.mark_resolved(p2, "applied")

        all_proposals = evolution_proposal.list_proposals(proposals_dir=tmp_path)
        assert len(all_proposals) == 2

        open_only = evolution_proposal.list_proposals(status="open", proposals_dir=tmp_path)
        assert len(open_only) == 1
        assert open_only[0]["title"] == "One"

        applied_only = evolution_proposal.list_proposals(status="applied", proposals_dir=tmp_path)
        assert len(applied_only) == 1
        assert applied_only[0]["title"] == "Two"

    def test_list_parses_tier_and_files_changed(self, tmp_path):
        evolution_proposal.write_proposal(
            title="Check fields", rationale="a",
            files_changed=["strategy.md", "params.json"], evidence="a", proposals_dir=tmp_path)
        [p] = evolution_proposal.list_proposals(proposals_dir=tmp_path)
        assert p["tier"] == "auto"
        assert p["files_changed"] == ["strategy.md", "params.json"]

    def test_empty_directory_returns_empty_list(self, tmp_path):
        assert evolution_proposal.list_proposals(proposals_dir=tmp_path / "nonexistent") == []

    def test_mark_resolved_appends_resolution_section_and_keeps_history(self, tmp_path):
        path = evolution_proposal.write_proposal(
            title="X", rationale="a", files_changed=["strategy.md"], evidence="a", proposals_dir=tmp_path)
        evolution_proposal.mark_resolved(path, "applied")
        text = path.read_text()
        assert "**Status**: applied" in text
        assert "## Resolution" in text
        assert "applied at" in text
        # Original content preserved, not overwritten.
        assert "## Rationale" in text
