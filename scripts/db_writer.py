#!/usr/bin/env python3
"""
db_writer.py — in-process write-behind buffer for trader_db.py (2026-07-28).

Ports the PATTERN from paper-trading-rebuild's src/data_bus.py DbWriteQueue
(in-process buffer, batched flush, failed batches re-enqueued) -- not a
dependency on that file or repo (this repo deliberately doesn't depend on
data_bus.py for anything live-critical, see skills/data-bus-fallback.md).

Real constraint this repo's architecture imposes, unlike data_bus.py's
long-running daemon-thread context: each tick_prompt.md step is typically
its own short-lived `exec` subprocess, not one continuous process spanning
a whole tick. So this buffer cannot batch writes ACROSS separate script
invocations -- it batches writes made WITHIN a single process's lifetime
(e.g. record_decision()'s decision-row + training-example-row insert) into
one commit instead of two. enqueue() never raises; flush_all() is
synchronous (no background thread -- there's no process to host one
between subprocess calls) and should be called once, at the end of
whatever logical operation queued the rows.
"""
import logging
import sqlite3
from collections import defaultdict
from pathlib import Path

import trader_db

log = logging.getLogger("db_writer")

_buffer: dict = defaultdict(list)

_INSERT_TEMPLATES = {
    "decisions": (
        "INSERT INTO decisions (ticker, timestamp, decision, conviction, rationale, regime, decision_json) "
        "VALUES (:ticker, :timestamp, :decision, :conviction, :rationale, :regime, :decision_json)"
    ),
    "journal": (
        "INSERT INTO journal (timestamp, ticker, decision, rationale, equity, drawdown_pct, decision_id) "
        "VALUES (:timestamp, :ticker, :decision, :rationale, :equity, :drawdown_pct, :decision_id) "
        "ON CONFLICT(timestamp) DO NOTHING"
    ),
    "training_examples": (
        "INSERT INTO training_examples "
        "(ticker, decision_id, trade_id, label_win, label_return_pct, label_horizon, features, created_at) "
        "VALUES (:ticker, :decision_id, :trade_id, :label_win, :label_return_pct, :label_horizon, :features, :created_at)"
    ),
    "news_cache": (
        "INSERT INTO news_cache "
        "(url, title, summary, source, published_at, collected_at, tickers, sentiment_score, full_text) "
        "VALUES (:url, :title, :summary, :source, :published_at, :collected_at, :tickers, :sentiment_score, :full_text) "
        "ON CONFLICT(url) DO NOTHING"
    ),
    "alpaca_audit_log": (
        "INSERT INTO alpaca_audit_log "
        "(timestamp, endpoint, method, request_summary, status_code, response_summary, latency_ms) "
        "VALUES (:timestamp, :endpoint, :method, :request_summary, :status_code, :response_summary, :latency_ms)"
    ),
}


def enqueue(table: str, row: dict) -> None:
    """Buffer one row for `table`. Never raises -- an unknown table name is
    logged and dropped rather than blocking the caller."""
    if table not in _INSERT_TEMPLATES:
        log.error("db_writer.enqueue: unknown table %r, row dropped", table)
        return
    _buffer[table].append(row)


def pending_count() -> int:
    return sum(len(rows) for rows in _buffer.values())


def flush_all(db_path: Path = None) -> bool:
    """Writes every buffered row in one transaction, clears the buffer on
    success. On failure (e.g. a busy-timeout'd WAL lock), logs and leaves
    the buffer intact so a caller can retry within the same process --
    never raises into the caller."""
    if not _buffer:
        return True
    try:
        conn = trader_db.get_conn(db_path)
    except Exception as e:
        log.error("db_writer.flush_all: could not open DB, %d rows still pending: %s", pending_count(), e)
        return False

    try:
        with conn:
            for table, rows in _buffer.items():
                if not rows:
                    continue
                conn.executemany(_INSERT_TEMPLATES[table], rows)
    except sqlite3.Error as e:
        log.error("db_writer.flush_all: write failed, %d rows still pending: %s", pending_count(), e)
        return False
    finally:
        conn.close()

    _buffer.clear()
    return True
