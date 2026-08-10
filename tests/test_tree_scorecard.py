#!/usr/bin/env python3
"""Unit tests for scripts/tree_scorecard.py: score_tree_nodes (pure scoring
logic) plus fetch_tagged_examples (real sqlite3 under tmp_path, same
migration pattern as test_signal_scorecard.py)."""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tree_scorecard  # noqa: E402
import trader_db  # noqa: E402


def ex(node_id, action_taken, label_win):
    return {"tree_node": node_id, "tree_action_taken": action_taken, "label_win": label_win}


class TestScoreTreeNodes:
    def test_followed_and_won_is_a_hit(self):
        examples = [ex("market_context_exit_v1", "followed", True)]
        result = tree_scorecard.score_tree_nodes(examples, min_samples=1)
        assert result["market_context_exit_v1"]["hit_rate"] == 1.0

    def test_followed_and_lost_is_a_miss(self):
        examples = [ex("market_context_exit_v1", "followed", False)]
        result = tree_scorecard.score_tree_nodes(examples, min_samples=1)
        assert result["market_context_exit_v1"]["hit_rate"] == 0.0

    def test_overridden_and_lost_is_a_hit(self):
        """Ignored a restraint node's caution, and it would have been
        right -- the restraint is vindicated."""
        examples = [ex("peaked_pump_chase_v1", "overridden", False)]
        result = tree_scorecard.score_tree_nodes(examples, min_samples=1)
        assert result["peaked_pump_chase_v1"]["hit_rate"] == 1.0

    def test_overridden_and_won_is_a_miss(self):
        """Ignored the restraint, worked out anyway -- a miss for the
        node's validity, even though the trade itself was fine."""
        examples = [ex("peaked_pump_chase_v1", "overridden", True)]
        result = tree_scorecard.score_tree_nodes(examples, min_samples=1)
        assert result["peaked_pump_chase_v1"]["hit_rate"] == 0.0

    def test_below_min_samples_flagged_insufficient(self):
        examples = [ex("market_context_exit_v1", "followed", True)]
        result = tree_scorecard.score_tree_nodes(examples, min_samples=10)
        assert result["market_context_exit_v1"]["status"] == "insufficient_data"
        assert "hit_rate" not in result["market_context_exit_v1"]

    def test_at_min_samples_gets_scored(self):
        examples = [ex("a", "followed", True) for _ in range(5)]
        result = tree_scorecard.score_tree_nodes(examples, min_samples=5)
        assert result["a"]["status"] == "scored"
        assert result["a"]["hit_rate"] == 1.0

    def test_multiple_nodes_tracked_independently(self):
        examples = [
            ex("market_context_exit_v1", "followed", True),
            ex("peaked_pump_chase_v1", "overridden", False),
        ]
        result = tree_scorecard.score_tree_nodes(examples, min_samples=1)
        assert result["market_context_exit_v1"]["hit_rate"] == 1.0
        assert result["peaked_pump_chase_v1"]["hit_rate"] == 1.0

    def test_followed_and_overridden_counts_tracked(self):
        examples = [
            ex("a", "followed", True),
            ex("a", "followed", False),
            ex("a", "overridden", False),
        ]
        result = tree_scorecard.score_tree_nodes(examples, min_samples=1)
        assert result["a"]["followed"] == 2
        assert result["a"]["overridden"] == 1
        assert result["a"]["n"] == 3

    def test_missing_node_id_ignored(self):
        examples = [{"tree_node": None, "tree_action_taken": "followed", "label_win": True}]
        assert tree_scorecard.score_tree_nodes(examples, min_samples=1) == {}

    def test_unrecognized_action_taken_ignored(self):
        examples = [ex("a", "partial", True)]
        assert tree_scorecard.score_tree_nodes(examples, min_samples=1) == {}

    def test_no_examples_returns_empty(self):
        assert tree_scorecard.score_tree_nodes([], min_samples=1) == {}


class TestFetchTaggedExamples:
    def test_returns_only_tree_tagged_labeled_rows(self, tmp_path):
        db_path = tmp_path / "trader.db"
        conn = trader_db.get_conn(db_path)
        tagged = trader_db.insert_training_example(
            conn, ticker="AAA",
            features='{"tree_node": "peaked_pump_chase_v1", "tree_action_taken": "followed"}',
            created_at="t1",
        )
        untagged = trader_db.insert_training_example(
            conn, ticker="BBB", features='{"technical": {"direction": "bullish"}}', created_at="t1",
        )
        trader_db.label_training_example(conn, tagged, trade_id=None, label_win=1, label_return_pct=1.0)
        trader_db.label_training_example(conn, untagged, trade_id=None, label_win=1, label_return_pct=1.0)
        conn.close()

        examples = tree_scorecard.fetch_tagged_examples(db_path=db_path)
        assert len(examples) == 1
        assert examples[0]["tree_node"] == "peaked_pump_chase_v1"
        assert examples[0]["tree_action_taken"] == "followed"
        assert examples[0]["label_win"] == 1

    def test_malformed_json_row_skipped_not_raised(self, tmp_path):
        db_path = tmp_path / "trader.db"
        conn = trader_db.get_conn(db_path)
        te_id = trader_db.insert_training_example(conn, ticker="AAA", features="not valid json", created_at="t1")
        trader_db.label_training_example(conn, te_id, trade_id=None, label_win=1, label_return_pct=1.0)
        conn.close()

        assert tree_scorecard.fetch_tagged_examples(db_path=db_path) == []

    def test_get_conn_failure_returns_empty_list_not_raise(self, monkeypatch, tmp_path):
        def raise_get_conn(db_path=None):
            raise ConnectionError("simulated DB outage")
        monkeypatch.setattr(tree_scorecard.trader_db, "get_conn", raise_get_conn)
        assert tree_scorecard.fetch_tagged_examples(db_path=tmp_path / "x.db") == []

    def test_query_failure_returns_empty_list_not_raise(self, monkeypatch, tmp_path):
        def raise_fetch(conn, label_horizon=None):
            raise RuntimeError("simulated query failure")
        monkeypatch.setattr(tree_scorecard.trader_db, "fetch_labeled_training_examples", raise_fetch)
        assert tree_scorecard.fetch_tagged_examples(db_path=tmp_path / "trader.db") == []
