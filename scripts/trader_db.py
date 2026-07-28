#!/usr/bin/env python3
"""
trader_db.py — local SQLite trading-state store for Stan (2026-07-28).

Replaces the remote Postgres tables on docker.klo (trading.decisions,
trading.journal, trading.training_examples, public.news_cache) that
scripts/db.py/decisions.py/news_collector.py wrote to. Same WAL/busy_timeout
connection pattern as discovery_db.py -- Stan is the sole consumer now, so
no trader_id/multi-tenant column (that was kairos/aldridge-era schema
cruft). Plain SQL types throughout so a later per-table migration back to
Postgres, if the local disk ever genuinely outgrows it, stays a mechanical
step rather than a rewrite.

Nothing calls this yet -- lands unwired, mirroring discovery_db.py's own
landing before discovery_daemon.py existed.
"""
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "state" / "trader.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    decision        TEXT NOT NULL,
    conviction      REAL,
    rationale       TEXT,
    regime          TEXT,
    decision_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker_ts ON decisions(ticker, timestamp);

CREATE TABLE IF NOT EXISTS journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    ticker          TEXT,
    decision        TEXT,
    rationale       TEXT,
    equity          REAL,
    drawdown_pct    REAL,
    decision_id     INTEGER REFERENCES decisions(id),
    UNIQUE(timestamp)
);

CREATE TABLE IF NOT EXISTS training_examples (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    decision_id         INTEGER REFERENCES decisions(id),
    trade_id            TEXT,
    label_win           INTEGER,
    label_return_pct    REAL,
    label_horizon       TEXT,
    features            TEXT NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_training_examples_ticker_unlabeled ON training_examples(ticker, label_win);

CREATE TABLE IF NOT EXISTS news_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    summary         TEXT,
    source          TEXT NOT NULL,
    published_at    TEXT NOT NULL,
    collected_at    TEXT NOT NULL,
    tickers         TEXT,
    sentiment_score REAL DEFAULT 0.0,
    full_text       TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_cache_published ON news_cache(published_at DESC);

CREATE TABLE IF NOT EXISTS alpaca_audit_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,
    endpoint          TEXT NOT NULL,
    method            TEXT NOT NULL,
    request_summary   TEXT,
    status_code       INTEGER,
    response_summary  TEXT,
    latency_ms        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alpaca_audit_ts ON alpaca_audit_log(timestamp);
"""


def get_conn(db_path: Path = None) -> sqlite3.Connection:
    """WAL-mode connection with a busy_timeout so the tick loop and any
    off-hours reader (signal_scorecard.py, manual sqlite3 inspection)
    don't collide. Creates the schema if missing. db_path override lets
    tests/dry-runs point at a scratch file."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def insert_decision(conn: sqlite3.Connection, ticker: str, timestamp: str, decision: str,
                     conviction: float = None, rationale: str = "", regime: str = None,
                     decision_json: str = None) -> int:
    with conn:
        cur = conn.execute(
            """INSERT INTO decisions (ticker, timestamp, decision, conviction, rationale, regime, decision_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker, timestamp, decision, conviction, rationale, regime, decision_json),
        )
        return cur.lastrowid


def insert_journal_entry(conn: sqlite3.Connection, timestamp: str, ticker: str = None,
                          decision: str = None, rationale: str = "", equity: float = 0.0,
                          drawdown_pct: float = 0.0, decision_id: int = None):
    with conn:
        cur = conn.execute(
            """INSERT INTO journal (timestamp, ticker, decision, rationale, equity, drawdown_pct, decision_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(timestamp) DO NOTHING""",
            (timestamp, ticker, decision, rationale, equity, drawdown_pct, decision_id),
        )
        return cur.lastrowid if cur.rowcount else None


def insert_training_example(conn: sqlite3.Connection, ticker: str, features: str,
                             decision_id: int = None, trade_id: str = None,
                             label_win: int = None, label_return_pct: float = None,
                             label_horizon: str = None, created_at: str = None) -> int:
    with conn:
        cur = conn.execute(
            """INSERT INTO training_examples
               (ticker, decision_id, trade_id, label_win, label_return_pct, label_horizon, features, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, decision_id, trade_id, label_win, label_return_pct, label_horizon, features, created_at),
        )
        return cur.lastrowid


def label_training_example(conn: sqlite3.Connection, training_example_id: int, trade_id: str,
                            label_win: int, label_return_pct: float) -> None:
    with conn:
        conn.execute(
            """UPDATE training_examples
               SET trade_id = ?, label_win = ?, label_return_pct = ?, label_horizon = 'trade_close'
               WHERE id = ?""",
            (trade_id, label_win, label_return_pct, training_example_id),
        )


def latest_unlabeled_training_example(conn: sqlite3.Connection, ticker: str):
    row = conn.execute(
        """SELECT id FROM training_examples
           WHERE ticker = ? AND label_win IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        (ticker,),
    ).fetchone()
    return row["id"] if row else None


def fetch_labeled_training_examples(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        "SELECT features, label_win FROM training_examples WHERE label_win IS NOT NULL"
    ).fetchall()
    return [{"features": r["features"], "label_win": r["label_win"]} for r in rows]


def upsert_news_articles(conn: sqlite3.Connection, articles: list) -> int:
    """Insert new articles, skip existing (matched by url). Returns count
    actually inserted, mirroring the old upsert_articles()'s ON CONFLICT
    DO NOTHING semantics/return contract."""
    inserted = 0
    with conn:
        for a in articles:
            cur = conn.execute(
                """INSERT INTO news_cache
                   (url, title, summary, source, published_at, collected_at, tickers, sentiment_score, full_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(url) DO NOTHING""",
                (
                    a["url"], a["title"], a.get("summary"), a["source"], a["published_at"],
                    a["collected_at"], a.get("tickers"), a.get("sentiment_score", 0.0), a.get("full_text"),
                ),
            )
            inserted += cur.rowcount
    return inserted


def recent_watchlist_articles(conn: sqlite3.Connection, tickers: list, hours: int = 24, now: str = None) -> list:
    """tickers is stored as a JSON-array string per row; SQLite has no
    array-overlap operator, so this filters in Python after a time-bounded
    fetch rather than trying to push the overlap check into SQL."""
    import datetime
    import json as _json
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    wanted = {t.upper() for t in tickers}
    rows = conn.execute(
        """SELECT title, tickers, sentiment_score, source, published_at FROM news_cache
           WHERE (julianday(?) - julianday(published_at)) * 24 <= ?
           ORDER BY published_at DESC""",
        (now, hours),
    ).fetchall()
    result = []
    for r in rows:
        try:
            row_tickers = _json.loads(r["tickers"]) if r["tickers"] else []
        except (ValueError, TypeError):
            row_tickers = []
        hits = [t for t in row_tickers if t.upper() in wanted]
        if not hits:
            continue
        result.append({
            "title": r["title"], "ticker_hits": hits, "sentiment": round(float(r["sentiment_score"]), 2),
            "source": r["source"], "published_at": r["published_at"],
        })
        if len(result) >= 50:
            break
    return result


def insert_alpaca_audit_row(conn: sqlite3.Connection, timestamp: str, endpoint: str, method: str,
                             request_summary: str = None, status_code: int = None,
                             response_summary: str = None, latency_ms: int = None) -> int:
    with conn:
        cur = conn.execute(
            """INSERT INTO alpaca_audit_log
               (timestamp, endpoint, method, request_summary, status_code, response_summary, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, endpoint, method, request_summary, status_code, response_summary, latency_ms),
        )
        return cur.lastrowid


def prune_alpaca_audit_log(conn: sqlite3.Connection, retention_days: int, now: str = None) -> int:
    """Deletes audit rows older than retention_days. Returns rows deleted."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        cur = conn.execute(
            "DELETE FROM alpaca_audit_log WHERE (julianday(?) - julianday(timestamp)) > ?",
            (now, retention_days),
        )
        return cur.rowcount
