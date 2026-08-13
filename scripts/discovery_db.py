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
import re
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
    sector              TEXT,
    industry            TEXT,
    market_cap          REAL,
    ma_filing_flag      TEXT,
    ma_filing_checked_at TEXT,
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


_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQL_COLTYPE_RE = re.compile(r"^[A-Za-z0-9_ ().+'-]+$")


def _migrate_add_column(conn: sqlite3.Connection, table: str, column: str, coltype_and_default: str) -> None:
    """Same pattern/rationale as trader_db.py's helper of the same name --
    CREATE TABLE IF NOT EXISTS only creates a column on a fresh DB, so an
    already-existing live discovery_pool.db needs this to actually gain a
    new column. Idempotent (checks PRAGMA table_info first). Identifiers
    are validated against a strict allowlist before being interpolated,
    same defense-in-depth rationale as the trader_db.py original."""
    for label, value in (("table", table), ("column", column)):
        if not _SQL_IDENTIFIER_RE.match(value):
            raise ValueError(f"_migrate_add_column: unsafe {label} name {value!r}")
    if not _SQL_COLTYPE_RE.match(coltype_and_default):
        raise ValueError(f"_migrate_add_column: unsafe coltype_and_default {coltype_and_default!r}")

    existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_and_default}")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # 2026-08-13: sector/industry/market_cap enrichment (yfinance, best-effort,
    # fetched once per candidate on first in-band transition) -- see
    # discovery_daemon.py's enrich_candidate_fundamentals(). NULL on every
    # pre-existing row and on any row where the fetch failed/was skipped.
    _migrate_add_column(conn, "candidates", "sector", "TEXT")
    _migrate_add_column(conn, "candidates", "industry", "TEXT")
    _migrate_add_column(conn, "candidates", "market_cap", "REAL")
    # 2026-08-13: BWMN data-gap fix -- free SEC EDGAR M&A filing check (see
    # scripts/edgar_scan.py), same best-effort/slow-cadence pattern as the
    # fundamentals columns above. ma_filing_checked_at (not just a NULL/non-
    # NULL flag) is what lets discovery_daemon.py's maybe_scan_edgar_filings()
    # re-check periodically instead of only ever checking once -- a ticker
    # with no filing today may have one next week.
    _migrate_add_column(conn, "candidates", "ma_filing_flag", "TEXT")
    _migrate_add_column(conn, "candidates", "ma_filing_checked_at", "TEXT")
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


def record_ma_filing_check(conn: sqlite3.Connection, ticker: str, flag, checked_at: str) -> None:
    """Records the result of an edgar_scan.py check -- flag is a short
    human-readable summary string (e.g. "8-K item 2.01 filed 2026-08-11")
    or None if nothing was found this check. checked_at always gets
    written regardless of outcome (it's the cadence-ordering signal
    maybe_scan_edgar_filings() reads, same role as watchlist_candidates'
    last_evaluated_at) -- a clean check must still update it, or a
    permanently-quiet ticker would look perpetually "never checked" and
    monopolize the front of the queue forever."""
    with conn:
        conn.execute(
            "UPDATE candidates SET ma_filing_flag = ?, ma_filing_checked_at = ? WHERE ticker = ?",
            (flag, checked_at, ticker),
        )


def record_fundamentals(conn: sqlite3.Connection, ticker: str, sector, industry, market_cap) -> None:
    """Best-effort enrichment write -- see discovery_daemon.py's
    enrich_candidate_fundamentals(). Any of the three fields may be None
    (fetch failed/partial); this overwrites unconditionally rather than
    COALESCE-on-update, since a re-fetch (e.g. after a ticker re-enters
    in_band following a long absence) should reflect the current read, not
    freeze the first one forever."""
    with conn:
        conn.execute(
            "UPDATE candidates SET sector = ?, industry = ?, market_cap = ? WHERE ticker = ?",
            (sector, industry, market_cap, ticker),
        )


# Composite ranking weights. Ranking used to be volume_ratio DESC alone,
# which handed the top of the promotion queue to whatever illiquid name
# had a single-day volume freak -- live top-of-queue was LFMDP/PSTR/CIGL/
# GFGF, several of them preferred shares or thin ETFs, some with a
# "confirming" headline that didn't mention the ticker at all. Volume is
# still the base signal but SATURATES, so a 20x freak on nothing can't
# outrank a 6x move that also has real sentiment and fresh news behind
# it. Max composite score is the sum of the three weights (2.0).
VOLUME_SATURATION_RATIO = 10.0   # volume_ratio at/above this scores a full 1.0
NEWS_RECENCY_WINDOW_HOURS = 24.0  # news decays linearly to 0 over this window
RANK_WEIGHT_VOLUME = 1.0
RANK_WEIGHT_SENTIMENT = 0.6      # ABS(sentiment) -- a strong bearish read is signal too
RANK_WEIGHT_NEWS_RECENCY = 0.4

# Each term is clamped to 0..1 before weighting, so no single component
# can dominate by being out of its expected range (volume_ratio is
# unbounded, sentiment is nominally -1..1 but comes from an LLM).
_RANK_SCORE_SQL = """
      :w_volume * MIN(COALESCE(volume_ratio, 0.0), :volume_saturation) / :volume_saturation
    + :w_sentiment * MIN(ABS(COALESCE(sentiment, 0.0)), 1.0)
    + :w_news * CASE
        WHEN news_confirmed_at IS NULL THEN 0.0
        ELSE MAX(0.0, MIN(1.0,
            1.0 - ((julianday(:now) - julianday(news_confirmed_at)) * 24.0) / :news_window))
      END
"""


def get_top_candidates(conn: sqlite3.Connection, limit: int, max_age_seconds: int, now: str) -> list:
    """The staleness-filtered ranking query -- single source of truth for
    'fresh enough to trust' and for what "top" means. now is an ISO
    timestamp string; SQLite's julianday() diff handles both the age
    comparison and the news-recency decay without needing Python datetime
    math inside the query.

    Rows come back with an extra 'rank_score' key (the composite defined
    above) so consumers can log/inspect why a candidate ranked where it
    did. volume_ratio DESC stays as the tiebreak, which keeps the pure-
    volume ordering intact for a pool that has no sentiment/news yet."""
    rows = conn.execute(
        f"""
        SELECT *, ({_RANK_SCORE_SQL}) AS rank_score
        FROM candidates
        WHERE in_band = 1
          AND (julianday(:now) - julianday(last_screened_at)) * 86400 <= :max_age_seconds
        ORDER BY rank_score DESC, volume_ratio IS NULL, volume_ratio DESC, ticker
        LIMIT :limit
        """,
        {
            "now": now,
            "max_age_seconds": max_age_seconds,
            "limit": limit,
            "w_volume": RANK_WEIGHT_VOLUME,
            "w_sentiment": RANK_WEIGHT_SENTIMENT,
            "w_news": RANK_WEIGHT_NEWS_RECENCY,
            "volume_saturation": VOLUME_SATURATION_RATIO,
            "news_window": NEWS_RECENCY_WINDOW_HOURS,
        },
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
