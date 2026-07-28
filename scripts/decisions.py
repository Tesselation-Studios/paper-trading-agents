"""record_decision / record_journal / record_trade_close — write to the
local trader_db.py store (state/trader.db), 2026-07-28.

Was: writes to the remote paper-trading-rebuild Postgres schema on
docker.klo (trading.decisions/trading.journal/trading.training_examples).
Migrated to local SQLite -- same external contract (function signatures,
return-dict shape) so record_decision.py (the CLI called from
tick_prompt.md step 9 on every BUY/SELL) needed zero changes. trader_id is
still accepted for CLI/logging compatibility but no longer stored -- the
old schema's trader_id column was multi-tenant cruft from the
kairos/aldridge era; Stan is the sole consumer of this DB now.

Populates training_examples going forward: one row per record_decision
call (features snapshot, label fields null), labeled later by
record_trade_close once the position actually closes. No backfill of
historical data (including the old remote rows) -- this only starts
collecting from here on, same as before the migration.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import trader_db
import signals

log = logging.getLogger("decisions")


def record_decision(trader_id, ticker, action, rationale="", conviction=0.0,
                     regime=None, features=None, db_path: Path = None):
    """Write a decisions row + seed a training_examples row.

    features should ideally use signals.py's per-signal shape
    ({"sentiment": {"direction": ..., "confidence": ...}, "technical": {...}, ...})
    so training_examples rows are actually queryable per-signal later. This is
    a soft check, not enforced — free-form features still work, they just
    won't be usable for signal-level attribution."""
    features = features or {}
    warnings = signals.validate_signal_features(features)
    if warnings:
        log.warning("record_decision(%s/%s): features shape warnings: %s", trader_id, ticker, warnings)
    ts = datetime.now(timezone.utc).isoformat()

    try:
        conn = trader_db.get_conn(db_path)
    except Exception as e:
        log.error("record_decision(%s/%s): DB unavailable, not logged: %s", trader_id, ticker, e)
        return {"error": f"db unavailable: {e}", "decision_id": None, "training_example_id": None}

    try:
        decision_id = trader_db.insert_decision(
            conn, ticker=ticker, timestamp=ts, decision=action, conviction=conviction,
            rationale=rationale, regime=regime, decision_json=json.dumps({"features": features}),
        )
    except Exception as e:
        log.error("record_decision(%s/%s): DB write failed, not logged: %s", trader_id, ticker, e)
        return {"error": f"db write failed: {e}", "decision_id": None, "training_example_id": None}

    try:
        training_example_id = trader_db.insert_training_example(
            conn, ticker=ticker, features=json.dumps(features), decision_id=decision_id, created_at=ts,
        )
    except Exception as e:
        log.error("record_decision(%s/%s): training_example insert failed: %s", trader_id, ticker, e)
        training_example_id = None
    finally:
        conn.close()
    return {"decision_id": decision_id, "training_example_id": training_example_id}


def record_journal(trader_id, ticker, decision_text, rationale="", equity=0.0,
                    drawdown_pct=0.0, decision_id=None, db_path: Path = None):
    """Write a journal row."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        conn = trader_db.get_conn(db_path)
    except Exception as e:
        log.error("record_journal(%s/%s): DB unavailable, not logged: %s", trader_id, ticker, e)
        return {"error": f"db unavailable: {e}", "journal_id": None}

    try:
        journal_id = trader_db.insert_journal_entry(
            conn, timestamp=ts, ticker=ticker, decision=decision_text, rationale=rationale,
            equity=equity, drawdown_pct=drawdown_pct, decision_id=decision_id,
        )
        return {"journal_id": journal_id}
    except Exception as e:
        log.error("record_journal(%s/%s): DB write failed, not logged: %s", trader_id, ticker, e)
        return {"error": f"db write failed: {e}", "journal_id": None}
    finally:
        conn.close()


def record_trade_close(trader_id, ticker, trade_id, pnl, return_pct, db_path: Path = None):
    """Label the most recent unlabeled training_examples row for `ticker`
    now that a trade has actually closed. trade_id is opaque/optional --
    Stonks has no trades table of its own (see record_decision.py's CLI),
    passed through only for the training_examples.trade_id column."""
    try:
        conn = trader_db.get_conn(db_path)
    except Exception as e:
        log.error("record_trade_close(%s/%s): DB unavailable, not labeled: %s", trader_id, ticker, e)
        return {"error": f"db unavailable: {e}", "training_example_id": None, "labeled": False}

    try:
        training_example_id = trader_db.latest_unlabeled_training_example(conn, ticker)
        if training_example_id is None:
            return {"error": f"no unlabeled training_examples row found for {trader_id}/{ticker}",
                     "training_example_id": None, "labeled": False}
    except Exception as e:
        log.error("record_trade_close(%s/%s): DB query failed, not labeled: %s", trader_id, ticker, e)
        return {"error": f"db query failed: {e}", "training_example_id": None, "labeled": False}

    try:
        trader_db.label_training_example(
            conn, training_example_id=training_example_id, trade_id=trade_id,
            label_win=1 if (pnl is not None and pnl > 0) else 0, label_return_pct=return_pct,
        )
    except Exception as e:
        log.error("record_trade_close(%s/%s): label write failed: %s", trader_id, ticker, e)
        return {"error": f"label write failed: {e}", "training_example_id": training_example_id, "labeled": False}
    finally:
        conn.close()
    return {"training_example_id": training_example_id, "labeled": True}
