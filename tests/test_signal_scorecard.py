#!/usr/bin/env python3
"""Unit tests for scripts/signal_scorecard.py's pure scoring logic
(score_signals). No DB — fetch_labeled_examples (the only DB-touching
function) is exercised manually, not here."""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import signal_scorecard  # noqa: E402


def ex(features, label_win):
    return {"features": features, "label_win": label_win}


class TestScoreSignals:
    def test_bullish_hit_and_miss(self):
        examples = [
            ex({"technical": {"direction": "bullish", "confidence": 0.6}}, True),
            ex({"technical": {"direction": "bullish", "confidence": 0.6}}, False),
        ]
        result = signal_scorecard.score_signals(examples, min_samples=1)
        assert result["technical"]["n"] == 2
        assert result["technical"]["hit_rate"] == 0.5

    def test_bearish_hit_is_when_trade_loses(self):
        examples = [
            ex({"technical": {"direction": "bearish", "confidence": 0.6}}, False),  # hit
            ex({"technical": {"direction": "bearish", "confidence": 0.6}}, False),  # hit
            ex({"technical": {"direction": "bearish", "confidence": 0.6}}, True),   # miss
        ]
        result = signal_scorecard.score_signals(examples, min_samples=1)
        assert result["technical"]["n"] == 3
        assert result["technical"]["hit_rate"] == round(2 / 3, 4)

    def test_neutral_excluded_from_hit_rate(self):
        examples = [
            ex({"technical": {"direction": "neutral", "confidence": 0.3}}, True),
            ex({"technical": {"direction": "bullish", "confidence": 0.6}}, True),
        ]
        result = signal_scorecard.score_signals(examples, min_samples=1)
        assert result["technical"]["n"] == 1
        assert result["technical"]["neutral"] == 1
        assert result["technical"]["hit_rate"] == 1.0

    def test_below_min_samples_flagged_insufficient(self):
        examples = [ex({"technical": {"direction": "bullish", "confidence": 0.6}}, True)]
        result = signal_scorecard.score_signals(examples, min_samples=10)
        assert result["technical"]["status"] == "insufficient_data"
        assert "hit_rate" not in result["technical"]

    def test_at_min_samples_gets_scored(self):
        examples = [ex({"a": {"direction": "bullish", "confidence": 0.6}}, True) for _ in range(5)]
        result = signal_scorecard.score_signals(examples, min_samples=5)
        assert result["a"]["status"] == "scored"
        assert result["a"]["hit_rate"] == 1.0

    def test_multiple_signals_tracked_independently(self):
        examples = [
            ex({
                "technical": {"direction": "bullish", "confidence": 0.6},
                "sentiment": {"direction": "bearish", "confidence": 0.5},
            }, True),
        ]
        result = signal_scorecard.score_signals(examples, min_samples=1)
        assert result["technical"]["hit_rate"] == 1.0  # bullish + win
        assert result["sentiment"]["hit_rate"] == 0.0  # bearish + win = miss

    def test_non_signal_shaped_entries_ignored(self):
        examples = [ex({"note": "context only", "technical": {"direction": "bullish", "confidence": 0.6}}, True)]
        result = signal_scorecard.score_signals(examples, min_samples=1)
        assert "note" not in result
        assert "technical" in result

    def test_no_examples_returns_empty(self):
        assert signal_scorecard.score_signals([], min_samples=1) == {}

    def test_malformed_features_skipped_not_raised(self):
        examples = [{"features": "not a dict", "label_win": True}]
        result = signal_scorecard.score_signals(examples, min_samples=1)
        assert result == {}
