"""Tests for scripts/position_sizing.py -- the deterministic sizing-suggestion
helper (2026-08-11). Pure-function tests pass an explicit `risk` dict so they
don't depend on the real params.json; the one CLI/live-fetch test monkeypatches
get_account."""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import position_sizing  # noqa: E402

RISK = {
    "probe_position_pct": 1.5,
    "probe_max_dollars": 150.0,
    "max_position_pct": 6.0,
    "long_play": {"position_size_pct": 3.0},
    "conviction_play": {"position_size_pct": 20.0},
}


class TestSuggestShares:
    def test_conviction_tier_targets_its_pct(self):
        # 20% of $10420 = $2084 -> floor($2084 / $301.56) = 6 shares
        result = position_sizing.suggest_shares("conviction", 301.56, 10420, risk=RISK)
        assert result["shares"] == 6
        assert result["target_pct"] == 20.0
        assert 12.0 <= result["pct_of_portfolio"] <= 20.0  # matches decision_heuristics.md's 12-20% guidance

    def test_standard_tier_uses_max_position_pct(self):
        # 6% of $10000 = $600 -> floor($600/$100) = 6 shares
        result = position_sizing.suggest_shares("standard", 100.0, 10000, risk=RISK)
        assert result["shares"] == 6
        assert result["target_pct"] == 6.0

    def test_long_tier_uses_long_play_pct(self):
        result = position_sizing.suggest_shares("long", 50.0, 10000, risk=RISK)
        assert result["target_pct"] == 3.0

    def test_probe_scales_with_price_cheap_ticker(self):
        # 1.5% of $10420 = $156.30, capped at probe_max_dollars=$150 -> floor($150/$1.54)
        result = position_sizing.suggest_shares("probe", 1.54, 10420, risk=RISK)
        assert result["shares"] == 97
        assert result["dollar_value"] <= 150.0

    def test_probe_floors_to_one_share_on_expensive_ticker(self):
        # SPY-style case: probe target ($150) is less than one share ($773) --
        # floors to 1 share and reports the real (oversized-for-probe) pct
        # rather than silently returning 0.
        result = position_sizing.suggest_shares("probe", 773.09, 10420, risk=RISK)
        assert result["shares"] == 1
        assert result["pct_of_portfolio"] == pytest.approx(7.42, abs=0.01)

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError, match="unknown tier"):
            position_sizing.suggest_shares("bogus", 100.0, 10000, risk=RISK)

    def test_non_positive_price_raises(self):
        with pytest.raises(ValueError):
            position_sizing.suggest_shares("standard", 0.0, 10000, risk=RISK)

    def test_non_positive_portfolio_value_raises(self):
        with pytest.raises(ValueError):
            position_sizing.suggest_shares("standard", 100.0, 0.0, risk=RISK)


class TestGraduationReadiness:
    """2026-08-12: is there real evidence yet to lean on conviction/long-play
    sizing, or is probe-everywhere still the honest default. Uses tmp_path
    scratch files, never the real state/*.json."""

    def _write(self, path, data):
        path.write_text(json.dumps(data))

    def test_no_files_at_all_not_ready(self, tmp_path):
        result = position_sizing.graduation_readiness(
            signal_path=tmp_path / "nope_signal.json", tree_path=tmp_path / "nope_tree.json",
        )
        assert result["ready"] is False
        assert result["qualifying_signals"] == {}
        assert result["qualifying_tree_nodes"] == {}

    def test_scored_signal_below_bar_not_ready(self, tmp_path):
        """The real 2026-08-12 case: technical has n=23 (scored) but
        hit_rate 0.3043, well under the 0.70 bar -- clearing min_samples
        and clearing the hit_rate bar are different things."""
        signal_path = tmp_path / "signal_scorecard.json"
        self._write(signal_path, {"signals": {
            "technical": {"n": 23, "status": "scored", "hit_rate": 0.3043},
            "regime": {"n": 9, "status": "insufficient_data"},
        }})
        result = position_sizing.graduation_readiness(
            signal_path=signal_path, tree_path=tmp_path / "nope_tree.json",
        )
        assert result["ready"] is False
        assert result["qualifying_signals"] == {}

    def test_scored_signal_above_bar_is_ready(self, tmp_path):
        signal_path = tmp_path / "signal_scorecard.json"
        self._write(signal_path, {"signals": {
            "technical": {"n": 23, "status": "scored", "hit_rate": 0.3043},
            "narrative": {"n": 15, "status": "scored", "hit_rate": 0.80},
        }})
        result = position_sizing.graduation_readiness(
            signal_path=signal_path, tree_path=tmp_path / "nope_tree.json",
        )
        assert result["ready"] is True
        assert set(result["qualifying_signals"]) == {"narrative"}

    def test_tree_node_above_bar_is_ready_even_with_no_signal_file(self, tmp_path):
        tree_path = tmp_path / "tree_scorecard.json"
        self._write(tree_path, {"nodes": {
            "deployment_pressure_override_v0": {"n": 12, "status": "scored", "hit_rate": 0.9167},
        }})
        result = position_sizing.graduation_readiness(
            signal_path=tmp_path / "nope_signal.json", tree_path=tree_path,
        )
        assert result["ready"] is True
        assert set(result["qualifying_tree_nodes"]) == {"deployment_pressure_override_v0"}

    def test_exactly_at_bar_counts_as_qualifying(self, tmp_path):
        signal_path = tmp_path / "signal_scorecard.json"
        self._write(signal_path, {"signals": {
            "sentiment": {"n": 10, "status": "scored", "hit_rate": 0.70},
        }})
        result = position_sizing.graduation_readiness(
            signal_path=signal_path, tree_path=tmp_path / "nope_tree.json",
        )
        assert result["ready"] is True

    def test_custom_hit_rate_bar_overrides_default(self, tmp_path):
        signal_path = tmp_path / "signal_scorecard.json"
        self._write(signal_path, {"signals": {
            "technical": {"n": 23, "status": "scored", "hit_rate": 0.3043},
        }})
        result = position_sizing.graduation_readiness(
            hit_rate_bar=0.30, signal_path=signal_path, tree_path=tmp_path / "nope_tree.json",
        )
        assert result["ready"] is True
        assert result["hit_rate_bar"] == 0.30

    def test_malformed_json_treated_as_no_evidence_not_a_crash(self, tmp_path):
        signal_path = tmp_path / "signal_scorecard.json"
        signal_path.write_text("{not valid json")
        result = position_sizing.graduation_readiness(
            signal_path=signal_path, tree_path=tmp_path / "nope_tree.json",
        )
        assert result["ready"] is False


class TestMainLivePortfolioValue:
    def test_fetches_live_equity_when_not_given(self, monkeypatch, capsys):
        monkeypatch.setattr(position_sizing, "get_account", lambda account: {"equity": "10420.00"})
        monkeypatch.setattr(position_sizing, "load_params", lambda: {"risk": RISK})
        monkeypatch.setattr(sys, "argv", ["position_sizing.py", "--tier", "conviction", "--price", "301.56"])

        rc = position_sizing.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert '"shares": 6' in out
