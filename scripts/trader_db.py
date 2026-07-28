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

-- positions: replaces positions/*.md's structured fields (entry price,
-- shares, sector, thesis). Deliberately does NOT store current
-- price/market_value/unrealized_pnl -- Alpaca stays the live source of
-- truth for those, matching the existing "thesis storage, not a price
-- mirror" principle (tick_prompt.md step 9).
CREATE TABLE IF NOT EXISTS positions (
    ticker          TEXT PRIMARY KEY,
    shares          REAL NOT NULL,
    entry_price     REAL NOT NULL,
    entry_time      TEXT NOT NULL,
    sector          TEXT,
    thesis          TEXT,
    status          TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'closed'
    closed_at       TEXT,
    close_reason    TEXT,
    realized_pnl    REAL,
    realized_return_pct REAL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);

-- watchlist_candidates: replaces strategies/watchlist.md's ## Candidates
-- section. note holds Stan's own reasoning text (why it's on the list,
-- signal read), same role watchlist.md's inline note played.
CREATE TABLE IF NOT EXISTS watchlist_candidates (
    ticker          TEXT PRIMARY KEY,
    price           REAL,
    rsi             REAL,
    volume_ratio    REAL,
    macd_hist       REAL,
    idle_ticks      INTEGER NOT NULL DEFAULT 0,
    source          TEXT,
    note            TEXT,
    added_at        TEXT NOT NULL,
    last_touched_at TEXT NOT NULL
);

-- bankroll_state: singleton row (id=1), replaces bankroll.md's
-- regex-parsed header fields.
CREATE TABLE IF NOT EXISTS bankroll_state (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    ceiling                 REAL NOT NULL,
    growth_rate             REAL NOT NULL,
    decay_rate              REAL NOT NULL,
    target_profit_pct       REAL NOT NULL,
    closed_trades_session   INTEGER NOT NULL DEFAULT 0,
    wins_session            INTEGER NOT NULL DEFAULT 0,
    losses_session          INTEGER NOT NULL DEFAULT 0,
    net_pnl_session         REAL NOT NULL DEFAULT 0.0,
    total_deployed_session  REAL NOT NULL DEFAULT 0.0,
    lifetime_trades         INTEGER NOT NULL DEFAULT 0,
    lifetime_net_pnl        REAL NOT NULL DEFAULT 0.0,
    lifetime_wins           INTEGER NOT NULL DEFAULT 0,
    lifetime_losses         INTEGER NOT NULL DEFAULT 0,
    updated_at              TEXT NOT NULL
);

-- bankroll_history: replaces bankroll.md's ## History log lines.
CREATE TABLE IF NOT EXISTS bankroll_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    label           TEXT NOT NULL,  -- 'WIN' | 'LOSS'
    pnl             REAL NOT NULL,
    ceiling_after   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bankroll_history_ts ON bankroll_history(timestamp);
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


# ── Positions ─────────────────────────────────────────────────────────────
# Deliberately no current_price/market_value/unrealized_pnl columns --
# Alpaca is the live source of truth for those (tick_prompt.md step 9's
# "thesis storage, not a price mirror" principle carries over unchanged).

def upsert_position(conn: sqlite3.Connection, ticker: str, shares: float, entry_price: float,
                     entry_time: str, sector: str = None, thesis: str = None, now: str = None) -> None:
    """Open a new position or update an existing one's thesis/shares (e.g.
    a scale-in). Does not touch status/close fields."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO positions (ticker, shares, entry_price, entry_time, sector, thesis, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'open', ?)
               ON CONFLICT(ticker) DO UPDATE SET
                   shares = excluded.shares,
                   sector = COALESCE(excluded.sector, positions.sector),
                   thesis = COALESCE(excluded.thesis, positions.thesis),
                   updated_at = excluded.updated_at""",
            (ticker, shares, entry_price, entry_time, sector, thesis, now),
        )


def close_position(conn: sqlite3.Connection, ticker: str, closed_at: str, close_reason: str,
                    realized_pnl: float, realized_return_pct: float) -> None:
    with conn:
        conn.execute(
            """UPDATE positions SET status = 'closed', closed_at = ?, close_reason = ?,
                   realized_pnl = ?, realized_return_pct = ?, updated_at = ?
               WHERE ticker = ?""",
            (closed_at, close_reason, realized_pnl, realized_return_pct, closed_at, ticker),
        )


def get_open_positions(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT * FROM positions WHERE status = 'open' ORDER BY entry_time").fetchall()
    return [dict(r) for r in rows]


def get_all_positions(conn: sqlite3.Connection) -> list:
    """Open + closed, no status filter -- used by merge_discoveries.py's
    dedup (a closed ticker shouldn't be re-added as a fresh candidate
    either) and by trader_query.py's closed-position lookup."""
    rows = conn.execute("SELECT * FROM positions ORDER BY entry_time").fetchall()
    return [dict(r) for r in rows]


def get_position(conn: sqlite3.Connection, ticker: str):
    row = conn.execute("SELECT * FROM positions WHERE ticker = ?", (ticker,)).fetchone()
    return dict(row) if row else None


# ── Watchlist candidates ─────────────────────────────────────────────────

def upsert_watchlist_candidate(conn: sqlite3.Connection, ticker: str, price: float = None,
                                rsi: float = None, volume_ratio: float = None, macd_hist: float = None,
                                source: str = None, note: str = None, now: str = None) -> None:
    """Adds a new candidate or touches an existing one -- idle_ticks resets
    to 0 on touch, matching watchlist.md's existing convention."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO watchlist_candidates
                   (ticker, price, rsi, volume_ratio, macd_hist, idle_ticks, source, note, added_at, last_touched_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                   price = excluded.price,
                   rsi = excluded.rsi,
                   volume_ratio = excluded.volume_ratio,
                   macd_hist = excluded.macd_hist,
                   idle_ticks = 0,
                   source = COALESCE(excluded.source, watchlist_candidates.source),
                   note = COALESCE(excluded.note, watchlist_candidates.note),
                   last_touched_at = excluded.last_touched_at""",
            (ticker, price, rsi, volume_ratio, macd_hist, source, note, now, now),
        )


def increment_idle_ticks(conn: sqlite3.Connection, except_tickers: list = None) -> None:
    """Bumps idle_ticks for every candidate not explicitly touched this
    tick. Call once per tick after any upsert_watchlist_candidate() calls
    for names actually reasoned about."""
    except_tickers = except_tickers or []
    with conn:
        if except_tickers:
            placeholders = ",".join("?" * len(except_tickers))
            conn.execute(
                f"UPDATE watchlist_candidates SET idle_ticks = idle_ticks + 1 WHERE ticker NOT IN ({placeholders})",
                except_tickers,
            )
        else:
            conn.execute("UPDATE watchlist_candidates SET idle_ticks = idle_ticks + 1")


def drop_stale_watchlist_candidates(conn: sqlite3.Connection, idle_ticks_threshold: int) -> list:
    """Deletes candidates at/over the idle threshold. Returns dropped tickers."""
    with conn:
        rows = conn.execute(
            "SELECT ticker FROM watchlist_candidates WHERE idle_ticks >= ?", (idle_ticks_threshold,)
        ).fetchall()
        dropped = [r["ticker"] for r in rows]
        conn.execute("DELETE FROM watchlist_candidates WHERE idle_ticks >= ?", (idle_ticks_threshold,))
    return dropped


def get_watchlist_candidates(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT * FROM watchlist_candidates ORDER BY idle_ticks, added_at").fetchall()
    return [dict(r) for r in rows]


def get_watchlist_batch(conn: sqlite3.Connection, limit: int) -> list:
    """Most-neglected-first slice for bounded per-tick evaluation (2026-07-28,
    fixes stonks-tick blowing its cron timeout evaluating the full list every
    tick). Highest idle_ticks first -- pair with increment_idle_ticks(conn,
    except_tickers=<this batch's tickers>) after evaluating, which leaves the
    evaluated names flat while everyone else climbs, producing a round-robin
    over several ticks without a separate cursor/offset to track."""
    rows = conn.execute(
        "SELECT * FROM watchlist_candidates ORDER BY idle_ticks DESC, added_at ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def remove_watchlist_candidate(conn: sqlite3.Connection, ticker: str) -> None:
    """Used when a candidate is promoted into an actual position."""
    with conn:
        conn.execute("DELETE FROM watchlist_candidates WHERE ticker = ?", (ticker,))


# ── Bankroll ──────────────────────────────────────────────────────────────
# Singleton row (id=1). Caller (bankroll.py, once migrated) owns the
# growth/decay math -- these are plain read/write, no business logic here.

def get_bankroll_state(conn: sqlite3.Connection):
    row = conn.execute("SELECT * FROM bankroll_state WHERE id = 1").fetchone()
    return dict(row) if row else None


def upsert_bankroll_state(conn: sqlite3.Connection, ceiling: float, growth_rate: float, decay_rate: float,
                           target_profit_pct: float, closed_trades_session: int = 0, wins_session: int = 0,
                           losses_session: int = 0, net_pnl_session: float = 0.0,
                           total_deployed_session: float = 0.0, lifetime_trades: int = 0,
                           lifetime_net_pnl: float = 0.0, lifetime_wins: int = 0, lifetime_losses: int = 0,
                           now: str = None) -> None:
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO bankroll_state
                   (id, ceiling, growth_rate, decay_rate, target_profit_pct, closed_trades_session,
                    wins_session, losses_session, net_pnl_session, total_deployed_session,
                    lifetime_trades, lifetime_net_pnl, lifetime_wins, lifetime_losses, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   ceiling = excluded.ceiling, growth_rate = excluded.growth_rate,
                   decay_rate = excluded.decay_rate, target_profit_pct = excluded.target_profit_pct,
                   closed_trades_session = excluded.closed_trades_session, wins_session = excluded.wins_session,
                   losses_session = excluded.losses_session, net_pnl_session = excluded.net_pnl_session,
                   total_deployed_session = excluded.total_deployed_session,
                   lifetime_trades = excluded.lifetime_trades, lifetime_net_pnl = excluded.lifetime_net_pnl,
                   lifetime_wins = excluded.lifetime_wins, lifetime_losses = excluded.lifetime_losses,
                   updated_at = excluded.updated_at""",
            (ceiling, growth_rate, decay_rate, target_profit_pct, closed_trades_session, wins_session,
             losses_session, net_pnl_session, total_deployed_session, lifetime_trades, lifetime_net_pnl,
             lifetime_wins, lifetime_losses, now),
        )


def record_bankroll_history(conn: sqlite3.Connection, timestamp: str, label: str, pnl: float,
                             ceiling_after: float) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO bankroll_history (timestamp, label, pnl, ceiling_after) VALUES (?, ?, ?, ?)",
            (timestamp, label, pnl, ceiling_after),
        )
        return cur.lastrowid


def get_bankroll_history(conn: sqlite3.Connection, limit: int = 50) -> list:
    rows = conn.execute(
        "SELECT * FROM bankroll_history ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
