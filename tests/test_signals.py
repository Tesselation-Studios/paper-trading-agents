#!/usr/bin/env python3
"""Unit tests for scripts/signals.py — schema validation and the
reconcile_signals() combiner, including the 2026-07-24 scorecard-weighting
addition. No network, no DB."""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import signals  # noqa: E402


class TestValidateSignalFeatures:
    def test_clean_features_no_warnings(self):
        features = {"technical": {"direction": "bullish", "confidence": 0.6}}
        assert signals.validate_signal_features(features) == []

    def test_bad_direction_warns(self):
        features = {"technical": {"direction": "up", "confidence": 0.6}}
        warnings = signals.validate_signal_features(features)
        assert len(warnings) == 1
        assert "direction" in warnings[0]

    def test_confidence_out_of_range_warns(self):
        features = {"technical": {"direction": "bullish", "confidence": 1.5}}
        warnings = signals.validate_signal_features(features)
        assert len(warnings) == 1
        assert "confidence" in warnings[0]

    def test_non_signal_shaped_entries_ignored(self):
        features = {"note": "just a string", "conviction": 0.7}
        assert signals.validate_signal_features(features) == []

    def test_non_dict_features_returns_error(self):
        warnings = signals.validate_signal_features("not a dict")
        assert len(warnings) == 1


class TestReconcileSignalsBaseline:
    def test_no_signals_returns_neutral(self):
        result = signals.reconcile_signals({})
        assert result["recommendation"] == "neutral"
        assert result["signal_count"] == 0

    def test_single_bullish_signal(self):
        result = signals.reconcile_signals(
            {"technical": {"direction": "bullish", "confidence": 0.8}}
        )
        assert result["recommendation"] == "bullish"
        assert result["agreement"] is True
        assert result["signal_count"] == 1

    def test_agreeing_signals_boost_confidence(self):
        result = signals.reconcile_signals({
            "technical": {"direction": "bullish", "confidence": 0.7},
            "sentiment": {"direction": "bullish", "confidence": 0.7},
        })
        assert result["recommendation"] == "bullish"
        assert result["agreement"] is True

    def test_conflicting_signals_erode_confidence(self):
        agree = signals.reconcile_signals({
            "a": {"direction": "bullish", "confidence": 0.8},
            "b": {"direction": "bullish", "confidence": 0.8},
        })
        conflict = signals.reconcile_signals({
            "a": {"direction": "bullish", "confidence": 0.8},
            "b": {"direction": "bearish", "confidence": 0.8},
        })
        assert conflict["agreement"] is False
        assert conflict["combined_confidence"] < agree["combined_confidence"]

    def test_non_signal_keys_ignored(self):
        result = signals.reconcile_signals({
            "technical": {"direction": "bullish", "confidence": 0.6},
            "note": "just context, not a scored signal",
        })
        assert result["signal_count"] == 1
        assert "note" not in result["detail"]

    def test_scorecard_none_matches_no_scorecard(self):
        features = {"technical": {"direction": "bullish", "confidence": 0.6}}
        assert signals.reconcile_signals(features) == signals.reconcile_signals(features, scorecard=None)


class TestReconcileSignalsWithScorecard:
    def test_insufficient_data_signal_unaffected(self):
        scorecard = {"technical": {"status": "insufficient_data", "n": 3}}
        with_sc = signals.reconcile_signals(
            {"technical": {"direction": "bullish", "confidence": 0.6}}, scorecard=scorecard
        )
        without_sc = signals.reconcile_signals(
            {"technical": {"direction": "bullish", "confidence": 0.6}}
        )
        assert with_sc["detail"]["technical"]["scorecard_multiplier"] == 1.0
        assert with_sc["combined_confidence"] == without_sc["combined_confidence"]

    def test_high_hit_rate_boosts_weight(self):
        scorecard = {"technical": {"status": "scored", "hit_rate": 0.9, "n": 20}}
        result = signals.reconcile_signals(
            {"technical": {"direction": "bullish", "confidence": 0.6}}, scorecard=scorecard
        )
        assert result["detail"]["technical"]["scorecard_multiplier"] > 1.0

    def test_low_hit_rate_dampens_weight(self):
        scorecard = {"technical": {"status": "scored", "hit_rate": 0.1, "n": 20}}
        result = signals.reconcile_signals(
            {"technical": {"direction": "bullish", "confidence": 0.6}}, scorecard=scorecard
        )
        assert result["detail"]["technical"]["scorecard_multiplier"] < 1.0

    def test_multiplier_clamped_at_extremes(self):
        scorecard = {"a": {"status": "scored", "hit_rate": 1.0, "n": 50}}
        result = signals.reconcile_signals(
            {"a": {"direction": "bullish", "confidence": 0.5}}, scorecard=scorecard
        )
        assert result["detail"]["a"]["scorecard_multiplier"] == signals._SCORECARD_MULT_MAX

    def test_signal_missing_from_scorecard_unaffected(self):
        scorecard = {"macro": {"status": "scored", "hit_rate": 0.9, "n": 20}}
        result = signals.reconcile_signals(
            {"technical": {"direction": "bullish", "confidence": 0.6}}, scorecard=scorecard
        )
        assert result["detail"]["technical"]["scorecard_multiplier"] == 1.0

    def test_original_confidence_preserved_in_detail(self):
        scorecard = {"technical": {"status": "scored", "hit_rate": 0.9, "n": 20}}
        result = signals.reconcile_signals(
            {"technical": {"direction": "bullish", "confidence": 0.6}}, scorecard=scorecard
        )
        # detail still reports the self-reported confidence, not the effective weight
        assert result["detail"]["technical"]["confidence"] == 0.6
