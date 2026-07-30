#!/usr/bin/env python3
"""
discovery_db.py — SQLite candidate-pool store for discovery_daemon.py's
continuous scanner, written 2026-07-27.

Rolling snapshot, not append-only history: every consumer (promote_candidates.py,
the staleness filter, a human inspecting the db) needs "what's the current
best-known state of ticker X," never a trend history of every past scan.
One row per ticker, UPSERTed on every screen -- including tickers that
FAIL the screen (in_band=0), so last_screened_at/screen_count stay
accurate for every ticker actually looked at, not just the winners. This
is what makes last_screened_at a real, mechanized staleness signal instead
of the LLM-prose "anything gone stale? drop it" judgment call this repo
relied on before.

This was a genuinely new persistence pattern for this repo at the time
(everything live here on 2026-07-27 was git-tracked flat files or Postgres
on docker.klo, no live SQLite) -- appropriate per the standing rule that
real/growing/queryable local datasets belong in SQLite, not JSON files.
2026-07-28 update: trader_db.py migrated decisions/journal/training_examples/
news_cache off docker.klo Postgres onto local SQLite too (see that module's
docstring), so docker.klo Postgres is no longer live for this repo at all --
noted here so this file doesn't read as the odd one out.
"""
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "state" / "discovery_pool.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    ticker              TEXT PRIMARY KEY,
    price               REAL NOT NULL,
    rsi                 REAL,
    volume_ratio        REAL,
    macd_hist           REAL,
    in_band             INTEGER NOT NULL DEFAULT 0,
    sentiment           REAL,
    news_headline       TEXT,
    news_confirmed_at   TEXT,
    first_seen_at       TEXT NOT NULL,
    last_screened_at    TEXT NOT NULL,
    last_in_band_at     TEXT,
    screen_count        INTEGER NOT NULL DEFAULT 0,
    universe_generation INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_promotable ON candidates(in_band, volume_ratio DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_last_screened ON candidates(last_screened_at);

CREATE TABLE IF NOT EXISTS universe_snapshot (
    position   INTEGER PRIMARY KEY,
    ticker     TEXT NOT NULL UNIQUE,
    generation INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def get_conn(db_path: Path = None) -> sqlite3.Connection:
    """WAL-mode connection with a busy_timeout so the daemon writer and
    reader scripts (promote_candidates.py, discovery_urgency_check.py,
    manual sqlite3 inspection) don't collide. Creates the schema if
    missing. db_path override lets tests/dry-runs point at a scratch file."""
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


def upsert_universe_snapshot(conn: sqlite3.Connection, tickers: list, generation: int, fetched_at: str) -> None:
    """Transactional replace: the universe list changes rarely (listings/
    delistings), so each refresh fully replaces the prior snapshot rather
    than trying to diff it."""
    with conn:
        conn.execute("DELETE FROM universe_snapshot")
        conn.executemany(
            "INSERT INTO universe_snapshot (position, ticker, generation, fetched_at) VALUES (?, ?, ?, ?)",
            [(i, t, generation, fetched_at) for i, t in enumerate(tickers)],
        )


def get_universe_snapshot(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT ticker FROM universe_snapshot ORDER BY position").fetchall()
    return [r["ticker"] for r in rows]


def get_universe_generation(conn: sqlite3.Connection):
    row = conn.execute("SELECT MAX(generation) AS gen FROM universe_snapshot").fetchone()
    return row["gen"] if row and row["gen"] is not None else None


def upsert_candidates(conn: sqlite3.Connection, candidates: list, universe_generation: int, screened_at: str) -> None:
    """Upserts one row per candidate dict. Each candidate must include
    'ticker'/'price' plus 'in_band' (bool) -- callers pass in_band=True for
    screen winners and in_band=False for tickers that were looked at but
    didn't pass, so last_screened_at/screen_count stay accurate for every
    ticker actually screened this cycle, not just the winners."""
    with conn:
        for c in candidates:
            in_band = 1 if c.get("in_band") else 0
            existing = conn.execute(
                "SELECT first_seen_at, screen_count FROM candidates WHERE ticker = ?", (c["ticker"],)
            ).fetchone()
            first_seen_at = existing["first_seen_at"] if existing else screened_at
            screen_count = (existing["screen_count"] if existing else 0) + 1
            conn.execute(
                """
                INSERT INTO candidates
                    (ticker, price, rsi, volume_ratio, macd_hist, in_band, first_seen_at,
                     last_screened_at, last_in_band_at, screen_count, universe_generation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    price = excluded.price,
                    rsi = excluded.rsi,
                    volume_ratio = excluded.volume_ratio,
                    macd_hist = excluded.macd_hist,
                    in_band = excluded.in_band,
                    last_screened_at = excluded.last_screened_at,
                    last_in_band_at = CASE WHEN excluded.in_band = 1
                                           THEN excluded.last_in_band_at
                                           ELSE candidates.last_in_band_at END,
                    screen_count = excluded.screen_count,
                    universe_generation = excluded.universe_generation
                """,
                (
                    c["ticker"], c["price"], c.get("rsi"), c.get("volume_ratio"), c.get("macd_hist"),
                    in_band, first_seen_at, screened_at,
                    screened_at if in_band else None,
                    screen_count, universe_generation,
                ),
            )


def record_news_confirmation(conn: sqlite3.Connection, ticker: str, sentiment, headline, confirmed_at: str) -> None:
    with conn:
        conn.execute(
            "UPDATE candidates SET sentiment = ?, news_headline = ?, news_confirmed_at = ? WHERE ticker = ?",
            (sentiment, headline, confirmed_at, ticker),
        )


def get_top_candidates(conn: sqlite3.Connection, limit: int, max_age_seconds: int, now: str) -> list:
    """The staleness-filtered ranking query -- single source of truth for
    'fresh enough to trust'. now is an ISO timestamp string; SQLite's
    julianday() diff handles the age comparison without needing Python
    datetime math inside the query."""
    rows = conn.execute(
        """
        SELECT * FROM candidates
        WHERE in_band = 1
          AND (julianday(?) - julianday(last_screened_at)) * 86400 <= ?
        ORDER BY volume_ratio IS NULL, volume_ratio DESC
        LIMIT ?
        """,
        (now, max_age_seconds, limit),
    ).fetchall()
    return [dict(r) for r in rows]


DEFAULT_FRESHNESS_MAX_AGE_SECONDS = 10800  # 3h, matches promote_candidates.py's default


def pool_stats(conn: sqlite3.Connection, now: str = None, max_age_seconds: int = DEFAULT_FRESHNESS_MAX_AGE_SECONDS) -> dict:
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_tracked,
            SUM(in_band) AS in_band_count,
            MAX(last_screened_at) AS most_recent_last_screened_at
        FROM candidates
        """
    ).fetchone()
    fresh_row = conn.execute(
        """
        SELECT COUNT(*) AS fresh_in_band_count FROM candidates
        WHERE in_band = 1 AND (julianday(?) - julianday(last_screened_at)) * 86400 <= ?
        """,
        (now, max_age_seconds),
    ).fetchone()
    return {
        "total_tracked": row["total_tracked"] or 0,
        "in_band_count": row["in_band_count"] or 0,
        "fresh_in_band_count": fresh_row["fresh_in_band_count"] or 0,
        "most_recent_last_screened_at": row["most_recent_last_screened_at"],
    }
