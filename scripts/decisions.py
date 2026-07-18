"""record_decision / record_journal — write to the same live tables the rest
of paper-trading-rebuild already writes to (trading.decisions, 1,106 rows;
trading.journal, 1,443 rows — confirmed the dominant/active tables, not the
newer/sparser trading.trader_decisions/trader_journal pair), so this stays
internally consistent with the FK targets in trading.training_examples.

Also populates trading.training_examples going forward: one row per
record_decision call (features snapshot, label fields null), labeled later
by record_trade_close once the position actually closes. No backfill of
historical data — this only starts collecting from here on.
"""
import json
import logging
from datetime import datetime, timezone

import db
import signals

log = logging.getLogger("decisions")


def record_decision(trader_id, ticker, action, rationale="", conviction=0.0,
                     regime=None, features=None):
    """Write to trading.decisions + seed a trading.training_examples row.

    features should ideally use signals.py's per-signal shape
    ({"sentiment": {"direction": ..., "confidence": ...}, "technical": {...}, ...})
    so training_examples rows are actually queryable per-signal later. This is
    a soft check, not enforced — free-form features still work, they just
    won't be usable for signal-level attribution."""
    features = features or {}
    warnings = signals.validate_signal_features(features)
    if warnings:
        log.warning("record_decision(%s/%s): features shape warnings: %s", trader_id, ticker, warnings)
    ts = datetime.now(timezone.utc)

    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO trading.decisions
               (trader_id, ticker, timestamp, decision, conviction, rationale,
                regime, decision_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (trader_id, ticker, ts, action, conviction, rationale, regime,
             json.dumps({"features": features})),
        )
        decision_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    training_example_id = db.insert_training_example(
        trader_id=trader_id, ticker=ticker, features=features,
        decision_id=decision_id,
    )
    return {"decision_id": decision_id, "training_example_id": training_example_id}


def record_journal(trader_id, ticker, decision_text, rationale="", equity=0.0,
                    drawdown_pct=0.0, decision_id=None):
    """Write to trading.journal."""
    ts = datetime.now(timezone.utc)
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO trading.journal
               (trader_id, timestamp, ticker, decision, rationale, equity,
                drawdown_pct, decision_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (trader_id, timestamp) DO NOTHING
               RETURNING id""",
            (trader_id, ts, ticker, decision_text, rationale, equity,
             drawdown_pct, decision_id),
        )
        row = cur.fetchone()
        conn.commit()
        return {"journal_id": row[0] if row else None}
    finally:
        conn.close()


def record_trade_close(trader_id, ticker, trade_id, pnl, return_pct):
    """Label the most recent unlabeled training_examples row for
    (trader_id, ticker) now that a trade has actually closed.

    trade_id must be the bigint trading.trades.id (the surrogate PK), not
    the separate VARCHAR trading.trades.trade_id business key. Get it from
    db.fetch_recent_trade(trader_id, ticker) rather than inventing one."""
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id FROM trading.training_examples
               WHERE trader_id = %s AND ticker = %s AND label_win IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (trader_id, ticker),
        )
        row = cur.fetchone()
        if not row:
            return {"error": f"no unlabeled training_examples row found for {trader_id}/{ticker}"}
        training_example_id = row[0]
    finally:
        conn.close()

    db.label_training_example(
        training_example_id=training_example_id, trade_id=trade_id,
        label_win=(pnl is not None and pnl > 0), label_return_pct=return_pct,
    )
    return {"training_example_id": training_example_id, "labeled": True}
