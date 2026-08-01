#!/usr/bin/env python3
"""Unit tests for scripts/signal_scorecard.py: score_signals (pure scoring
logic) plus fetch_labeled_examples (real sqlite3 under tmp_path since the
2026-07-28 migration off remote Postgres)."""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import signal_scorecard  # noqa: E402
import trader_db  # noqa: E402


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


class TestFetchLabeledExamples:
    """Migrated 2026-07-28 from remote Postgres to local trader_db.py --
    real sqlite3 under tmp_path, plus fail-open tests for a DB outage
    (still relevant locally: a corrupt/locked file, not just network)."""

    def test_returns_parsed_features_for_labeled_rows_only(self, tmp_path):
        db_path = tmp_path / "trader.db"
        conn = trader_db.get_conn(db_path)
        labeled = trader_db.insert_training_example(
            conn, ticker="AAA", features='{"technical": {"direction": "bullish"}}', created_at="t1",
        )
        trader_db.insert_training_example(
            conn, ticker="BBB", features='{"technical": {"direction": "bearish"}}', created_at="t1",
        )
        trader_db.label_training_example(conn, labeled, trade_id=None, label_win=1, label_return_pct=1.0)
        conn.close()

        examples = signal_scorecard.fetch_labeled_examples("stonks", db_path=db_path)
        assert len(examples) == 1
        assert examples[0]["features"] == {"technical": {"direction": "bullish"}}
        assert examples[0]["label_win"] == 1

    def test_malformed_json_row_skipped_not_raised(self, tmp_path):
        db_path = tmp_path / "trader.db"
        conn = trader_db.get_conn(db_path)
        te_id = trader_db.insert_training_example(conn, ticker="AAA", features="not valid json", created_at="t1")
        trader_db.label_training_example(conn, te_id, trade_id=None, label_win=1, label_return_pct=1.0)
        conn.close()

        assert signal_scorecard.fetch_labeled_examples("stonks", db_path=db_path) == []

    def test_get_conn_failure_returns_empty_list_not_raise(self, monkeypatch, tmp_path):
        def raise_get_conn(db_path=None):
            raise ConnectionError("simulated DB outage")
        monkeypatch.setattr(signal_scorecard.trader_db, "get_conn", raise_get_conn)
        assert signal_scorecard.fetch_labeled_examples("stonks", db_path=tmp_path / "x.db") == []

    def test_query_failure_returns_empty_list_not_raise(self, monkeypatch, tmp_path):
        def raise_fetch(conn):
            raise RuntimeError("simulated query failure")
        monkeypatch.setattr(signal_scorecard.trader_db, "fetch_labeled_training_examples", raise_fetch)
        assert signal_scorecard.fetch_labeled_examples("stonks", db_path=tmp_path / "trader.db") == []


class TestKeyNormalizationAtScoreTime:
    """2026-08-01: this tallies by exact key name, so rows already in the DB
    under `macdh`/`macd_hist`/`rsi` have to fold into `technical` here too —
    otherwise every historical row keeps its fragmented key and no signal
    ever reaches the min_samples threshold."""

    def test_alias_rows_fold_into_canonical_tally(self):
        examples = [
            ex({"technical": {"direction": "bullish", "confidence": 0.6}}, True),
            ex({"macd_hist": {"direction": "bullish", "confidence": 0.7}}, True),
            ex({"rsi": {"direction": "bullish", "confidence": 0.6}}, False),
        ]
        result = signal_scorecard.score_signals(examples, min_samples=3)
        assert set(result) == {"technical"}
        assert result["technical"]["n"] == 3
        assert result["technical"]["status"] == "scored"

    def test_volume_aliases_fold_together(self):
        examples = [
            ex({"volume": {"direction": "bullish", "confidence": 0.55}}, True),
            ex({"vol_ratio": {"direction": "bullish", "confidence": 0.5}}, True),
        ]
        result = signal_scorecard.score_signals(examples, min_samples=1)
        assert set(result) == {"volume"}
        assert result["volume"]["n"] == 2

    def test_exit_trigger_is_not_scored_as_a_predictor(self):
        """A stop_trigger describes why an exit fired, not a pre-trade read
        on the name — scoring it as a predictor is meaningless."""
        examples = [
            ex({"stop_trigger": {"direction": "bearish", "confidence": 1.0}}, False),
            ex({"technical": {"direction": "bullish", "confidence": 0.6}}, True),
        ]
        result = signal_scorecard.score_signals(examples, min_samples=1)
        assert "stop_trigger" not in result
        assert "technical" in result

    def test_unrecognized_signal_still_tallied_under_its_own_name(self):
        """Kept, not dropped — an unknown key is warned about at write time,
        never silently discarded from the data."""
        examples = [ex({"vibes": {"direction": "bullish", "confidence": 0.9}}, True)]
        result = signal_scorecard.score_signals(examples, min_samples=1)
        assert result["vibes"]["n"] == 1
