"""Postgres connection to the existing paper-trading-rebuild `trading` schema.

Reuses the same DSN pattern already used by ~/projects/trading-agent-prompts/
record_decision.py and record_journal.py (host=docker.klo port=5433
dbname=trading user=trader — no password needed from this LAN host).

This module's own writes are additive-only (trading.training_examples).
decisions.py (same directory) also writes to trading.decisions/trading.journal
using this connection — both are live, active tables other tooling reads too,
not a new/separate schema.
"""
import json
import os

import psycopg2

PG_DSN = os.getenv("PG_DSN", "host=docker.klo port=5433 dbname=trading user=trader")


def get_conn():
    return psycopg2.connect(PG_DSN, connect_timeout=5)


def insert_training_example(trader_id, ticker, features, decision_id=None,
                             trade_id=None, label_win=None, label_return_pct=None,
                             label_horizon=None):
    """Insert one row into trading.training_examples. Additive-only."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO trading.training_examples
               (trader_id, ticker, decision_id, trade_id, label_win,
                label_return_pct, label_horizon, features)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (trader_id, ticker, decision_id, trade_id, label_win,
             label_return_pct, label_horizon, json.dumps(features)),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    finally:
        conn.close()


def label_training_example(training_example_id, trade_id, label_win, label_return_pct):
    """Backfill the label columns once a trade closes."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE trading.training_examples
               SET trade_id = %s, label_win = %s, label_return_pct = %s,
                   label_horizon = 'trade_close'
               WHERE id = %s""",
            (trade_id, label_win, label_return_pct, training_example_id),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_recent_trade(trader_id, ticker):
    """Look up the most recent trade for (trader_id, ticker) to pair a closed
    trade back to its training_examples row. Read-only against the existing
    trading.trades table."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, pnl, return_pct, exit_time
               FROM trading.trades
               WHERE trader_id = %s AND ticker = %s AND exit_time IS NOT NULL
               ORDER BY exit_time DESC LIMIT 1""",
            (trader_id, ticker),
        )
        row = cur.fetchone()
        if not row:
            return None
        trade_id, pnl, return_pct, exit_time = row
        return {
            "trade_id": trade_id,
            "pnl": float(pnl) if pnl is not None else None,
            "return_pct": float(return_pct) if return_pct is not None else None,
            "exit_time": exit_time.isoformat() if exit_time else None,
        }
    finally:
        conn.close()
