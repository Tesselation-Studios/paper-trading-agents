#!/usr/bin/env python3
"""Tests for scripts/decisions.py (migrated 2026-07-28 from remote Postgres
to local trader_db.py). Real sqlite3 files under tmp_path via db_path=,
matching trader_db.py's own test convention -- plus fail-open tests for a
DB-unavailable scenario (mocked), since that risk carries over from the
Postgres days (tick_prompt.md step 9 calls this on every BUY/SELL)."""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import decisions  # noqa: E402
import trader_db  # noqa: E402


def _raise_get_conn(*args, **kwargs):
    raise ConnectionError("simulated DB outage")


class TestRecordDecision:
    def test_writes_decision_and_training_example(self, tmp_path):
        db_path = tmp_path / "trader.db"
        result = decisions.record_decision(
            trader_id="stonks", ticker="AAA", action="BUY", rationale="momentum entry",
            conviction=0.6, regime="momentum_bull", features={"technical": {"direction": "bullish"}},
            db_path=db_path,
        )
        assert result["decision_id"] is not None
        assert result["training_example_id"] is not None

        conn = trader_db.get_conn(db_path)
        try:
            row = conn.execute("SELECT * FROM decisions WHERE id = ?", (result["decision_id"],)).fetchone()
            assert row["ticker"] == "AAA"
            assert row["conviction"] == 0.6
        finally:
            conn.close()

    def test_get_conn_failure_returns_error_dict(self, monkeypatch, tmp_path):
        monkeypatch.setattr(decisions.trader_db, "get_conn", _raise_get_conn)
        result = decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY", db_path=tmp_path / "x.db")
        assert "error" in result
        assert result["decision_id"] is None
        assert result["training_example_id"] is None

    def test_training_example_failure_still_returns_decision_id(self, monkeypatch, tmp_path):
        real_insert = trader_db.insert_training_example

        def raise_insert(*a, **kw):
            raise RuntimeError("simulated training_examples failure")
        monkeypatch.setattr(decisions.trader_db, "insert_training_example", raise_insert)

        result = decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY", db_path=tmp_path / "trader.db")
        assert result["decision_id"] is not None
        assert result["training_example_id"] is None
        assert "error" not in result
        monkeypatch.setattr(decisions.trader_db, "insert_training_example", real_insert)


class TestRecordJournal:
    def test_writes_journal_entry(self, tmp_path):
        db_path = tmp_path / "trader.db"
        result = decisions.record_journal(
            trader_id="stonks", ticker="AAA", decision_text="HOLD", rationale="no change",
            equity=10415.0, drawdown_pct=0.0, db_path=db_path,
        )
        assert result["journal_id"] is not None

    def test_get_conn_failure_returns_error_dict(self, monkeypatch, tmp_path):
        monkeypatch.setattr(decisions.trader_db, "get_conn", _raise_get_conn)
        result = decisions.record_journal(trader_id="stonks", ticker="AAA", decision_text="HOLD", db_path=tmp_path / "x.db")
        assert "error" in result
        assert result["journal_id"] is None


class TestRecordTradeClose:
    def test_labels_most_recent_unlabeled_example(self, tmp_path):
        db_path = tmp_path / "trader.db"
        decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY", db_path=db_path)

        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=12.5, return_pct=4.2, db_path=db_path,
        )
        assert result["labeled"] is True

        conn = trader_db.get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM training_examples WHERE id = ?", (result["training_example_id"],)
            ).fetchone()
            assert row["label_win"] == 1
            assert row["label_return_pct"] == 4.2
        finally:
            conn.close()

    def test_loss_labels_win_zero(self, tmp_path):
        db_path = tmp_path / "trader.db"
        decisions.record_decision(trader_id="stonks", ticker="AAA", action="BUY", db_path=db_path)
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=-3.0, return_pct=-2.5, db_path=db_path,
        )
        conn = trader_db.get_conn(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM training_examples WHERE id = ?", (result["training_example_id"],)
            ).fetchone()
            assert row["label_win"] == 0
        finally:
            conn.close()

    def test_no_unlabeled_example_returns_error(self, tmp_path):
        db_path = tmp_path / "trader.db"
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="ZZZ", trade_id=None, pnl=1.0, return_pct=1.0, db_path=db_path,
        )
        assert "error" in result
        assert result["labeled"] is False

    def test_get_conn_failure_returns_error_dict(self, monkeypatch, tmp_path):
        monkeypatch.setattr(decisions.trader_db, "get_conn", _raise_get_conn)
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=1.0, return_pct=1.0, db_path=tmp_path / "x.db",
        )
        assert "error" in result
        assert result["labeled"] is False


class TestCanonicalizeFeatures:
    """2026-08-01: signal_scorecard.score_signals() tallies by exact key
    name, so `technical`/`macdh`/`macd_hist`/`rsi` logged as four spellings
    of one signal were four tallies that each stayed under the 10-sample
    significance threshold forever — every signal in
    state/signal_scorecard.json was permanently 'insufficient_data'."""

    def test_aliases_fold_into_canonical_name(self):
        features, warnings = decisions.canonicalize_features({
            "macd_hist": {"direction": "bullish", "confidence": 0.7},
            "rsi": {"direction": "bullish", "confidence": 0.6},
        })
        assert set(features) == {"technical"}
        assert warnings

    def test_canonical_keys_pass_through_silently(self):
        original = {"technical": {"direction": "bullish", "confidence": 0.6},
                     "sentiment": {"direction": "neutral", "confidence": 0.5}}
        features, warnings = decisions.canonicalize_features(original)
        assert features == original
        assert warnings == []

    def test_non_signal_shaped_entries_untouched(self):
        original = {"macdh": 0.255, "note": "context only",
                     "entry": {"price": 10.0, "qty": 3}}
        features, warnings = decisions.canonicalize_features(original)
        assert features == original
        assert warnings == []

    def test_unrecognized_signal_key_kept_but_warned(self):
        """Never dropped — this is live trading data. Loud instead."""
        features, warnings = decisions.canonicalize_features(
            {"vibes": {"direction": "bullish", "confidence": 0.9}})
        assert "vibes" in features
        assert any("unrecognized" in w for w in warnings)

    def test_several_aliases_of_one_signal_are_combined_losslessly(self):
        features, warnings = decisions.canonicalize_features({
            "technical": {"direction": "bullish", "confidence": 0.6},
            "rsi": {"direction": "bearish", "confidence": 0.9},
        })
        assert set(features) == {"technical"}
        # Disagreement between the two reads must not read as confident.
        assert features["technical"]["direction"] in ("bullish", "bearish", "neutral")
        assert set(features["technical"]["components"]) == {"technical", "rsi"}
        assert any("combined into one" in w for w in warnings)

    def test_agreeing_aliases_keep_their_direction(self):
        features, _ = decisions.canonicalize_features({
            "macdh": {"direction": "bullish", "confidence": 0.7},
            "rsi": {"direction": "bullish", "confidence": 0.6},
        })
        assert features["technical"]["direction"] == "bullish"

    def test_exit_trigger_keys_are_recognized_but_not_predictive(self):
        _, warnings = decisions.canonicalize_features(
            {"stop_trigger": {"direction": "bearish", "confidence": 1.0}})
        assert warnings == []
        assert "stop_trigger" in decisions.NON_PREDICTIVE_SIGNAL_KEYS


class TestRecordEntryExample:
    def test_writes_entry_row_without_any_decision_call(self, tmp_path):
        db_path = tmp_path / "trader.db"
        result = decisions.record_entry_example(
            ticker="AAA", features={"technical": {"direction": "bullish", "confidence": 0.6}},
            position_entry_time="t1", db_path=db_path)
        conn = trader_db.get_conn(db_path)
        try:
            row = conn.execute("SELECT * FROM training_examples WHERE id = ?",
                                (result["training_example_id"],)).fetchone()
        finally:
            conn.close()
        assert row["example_type"] == "entry"
        assert row["position_entry_time"] == "t1"
        assert row["label_win"] is None

    def test_scale_in_merges_into_same_row(self, tmp_path):
        """positions.entry_time doesn't move on a scale-in, so one position
        must still mean exactly one entry row."""
        db_path = tmp_path / "trader.db"
        first = decisions.record_entry_example(ticker="AAA", features={"entry": {"qty": 3}},
                                                position_entry_time="t1", db_path=db_path)
        second = decisions.record_entry_example(ticker="AAA", features={"entry": {"qty": 5}},
                                                 position_entry_time="t1", db_path=db_path)
        assert second["training_example_id"] == first["training_example_id"]
        assert second["training_example_merged"] is True

    def test_new_position_after_close_gets_its_own_row(self, tmp_path):
        db_path = tmp_path / "trader.db"
        first = decisions.record_entry_example(ticker="AAA", position_entry_time="t1", db_path=db_path)
        second = decisions.record_entry_example(ticker="AAA", position_entry_time="t2", db_path=db_path)
        assert second["training_example_id"] != first["training_example_id"]

    def test_db_failure_returns_error_not_raise(self, monkeypatch, tmp_path):
        monkeypatch.setattr(decisions.trader_db, "get_conn", _raise_get_conn)
        result = decisions.record_entry_example(ticker="AAA", db_path=tmp_path / "x.db")
        assert "error" in result


class TestEntryRowLabeling:
    """The core 2026-08-01 fix. Previously record_trade_close() labeled 'the
    latest unlabeled row for this ticker', which on a SELL is the SELL's own
    row ({"stop_trigger": ...}) rather than the BUY row carrying RSI/MACD/
    sentiment. Confirmed live: BUY rows sat at label_win=NULL forever while
    SELL rows got labeled in their place."""

    def test_close_labels_the_buy_row_not_the_sell_row(self, tmp_path):
        db_path = tmp_path / "trader.db"
        buy = decisions.record_decision(
            trader_id="stonks", ticker="AAA", action="BUY",
            features={"technical": {"direction": "bullish", "confidence": 0.6}},
            db_path=db_path)
        sell = decisions.record_decision(
            trader_id="stonks", ticker="AAA", action="SELL",
            features={"stop_trigger": {"direction": "bearish", "confidence": 1.0}},
            db_path=db_path)
        assert sell["training_example_id"] != buy["training_example_id"]

        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=-3.0, return_pct=-2.0,
            db_path=db_path)
        assert result["training_example_id"] == buy["training_example_id"]

        conn = trader_db.get_conn(db_path)
        try:
            rows = {r["id"]: r for r in conn.execute("SELECT * FROM training_examples").fetchall()}
        finally:
            conn.close()
        assert rows[buy["training_example_id"]]["label_win"] == 0
        assert rows[sell["training_example_id"]]["label_win"] is None

    def test_close_prefers_the_row_for_the_position_being_closed(self, tmp_path):
        db_path = tmp_path / "trader.db"
        old = decisions.record_entry_example(ticker="AAA", position_entry_time="t1", db_path=db_path)
        new = decisions.record_entry_example(ticker="AAA", position_entry_time="t2", db_path=db_path)
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=5.0, return_pct=3.0,
            db_path=db_path, position_entry_time="t1")
        assert result["training_example_id"] == old["training_example_id"]
        assert result["training_example_id"] != new["training_example_id"]

    def test_agent_decision_log_merges_into_executors_entry_row(self, tmp_path):
        """executor.py writes the row mechanically at fill time; a later
        record_decision.py call for the same position must enrich THAT row,
        not create a second one."""
        db_path = tmp_path / "trader.db"
        conn = trader_db.get_conn(db_path)
        trader_db.upsert_position(conn, ticker="AAA", shares=3.0, entry_price=10.0, entry_time="t1")
        conn.close()

        entry = decisions.record_entry_example(
            ticker="AAA", features={"entry": {"price": 10.0, "qty": 3}},
            position_entry_time="t1", db_path=db_path)
        logged = decisions.record_decision(
            trader_id="stonks", ticker="AAA", action="BUY",
            features={"technical": {"direction": "bullish", "confidence": 0.6}},
            db_path=db_path)

        assert logged["training_example_id"] == entry["training_example_id"]
        assert logged["training_example_merged"] is True
        conn = trader_db.get_conn(db_path)
        try:
            rows = conn.execute("SELECT * FROM training_examples").fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        import json as _json
        features = _json.loads(rows[0]["features"])
        assert features["technical"]["direction"] == "bullish"   # from the agent
        assert features["entry"]["qty"] == 3                     # from the executor

    def test_legacy_row_with_signals_still_labelable(self, tmp_path):
        """Pre-migration rows have example_type NULL. One carrying real
        scored signals is still the right row to label."""
        db_path = tmp_path / "trader.db"
        conn = trader_db.get_conn(db_path)
        legacy = trader_db.insert_training_example(
            conn, ticker="AAA", features='{"technical": {"direction": "bullish", "confidence": 0.6}}',
            created_at="2026-07-30T13:00:00")
        conn.close()
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=1.0, return_pct=1.0, db_path=db_path)
        assert result["training_example_id"] == legacy

    def test_legacy_exit_only_row_is_not_labeled(self, tmp_path):
        db_path = tmp_path / "trader.db"
        conn = trader_db.get_conn(db_path)
        trader_db.insert_training_example(
            conn, ticker="AAA", features='{"stop_trigger": {"direction": "bearish", "confidence": 1.0}}',
            created_at="2026-07-30T13:00:00")
        conn.close()
        result = decisions.record_trade_close(
            trader_id="stonks", ticker="AAA", trade_id=None, pnl=1.0, return_pct=1.0, db_path=db_path)
        assert result["labeled"] is False
        assert "error" in result

    def test_features_keys_normalized_on_write(self, tmp_path):
        db_path = tmp_path / "trader.db"
        result = decisions.record_decision(
            trader_id="stonks", ticker="AAA", action="BUY",
            features={"macd_hist": {"direction": "bullish", "confidence": 0.7}},
            db_path=db_path)
        conn = trader_db.get_conn(db_path)
        try:
            row = conn.execute("SELECT features FROM training_examples WHERE id = ?",
                                (result["training_example_id"],)).fetchone()
        finally:
            conn.close()
        import json as _json
        assert "technical" in _json.loads(row["features"])
