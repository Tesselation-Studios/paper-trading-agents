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
import datetime
import re
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

-- training_examples: one row per decision, labeled with the eventual
-- outcome once the position closes.
--
-- example_type ('entry' | 'exit' | 'observation', NULL on pre-2026-08-01
-- rows) and position_entry_time exist because "which row does a close
-- label?" was previously answered by "the newest unlabeled row for this
-- ticker" -- which on a SELL is the SELL's own row, not the BUY row that
-- actually carried the predictive signals. Only 'entry' rows are
-- label-eligible, and position_entry_time links a row to the exact
-- positions.entry_time it was opened for.
CREATE TABLE IF NOT EXISTS training_examples (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    decision_id         INTEGER REFERENCES decisions(id),
    trade_id            TEXT,
    label_win           INTEGER,
    label_return_pct    REAL,
    label_horizon       TEXT,
    features            TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    example_type        TEXT,
    position_entry_time TEXT
);
CREATE INDEX IF NOT EXISTS idx_training_examples_ticker_unlabeled ON training_examples(ticker, label_win);
-- NOTE: the (ticker, example_type, label_win) index is created in
-- init_schema() AFTER the column migration, not here. On an existing DB
-- the CREATE TABLE above is a no-op, so an index over a column this
-- script is about to add would fail with "no such column" before the
-- migration ever ran.

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

-- position_thesis_log (2026-08-03): append-only history of what was
-- claimed about a position and when it was re-checked -- positions.thesis
-- is a single blob that gets silently overwritten on every update (a scale-in
-- note replaces the original entry rationale with no trace), which is fine
-- for "what do I believe right now" but useless for "did my read hold up
-- over time". Same current-state-table/append-only-log split already used
-- for positions/decisions vs journal. signals_snapshot is the reconcile()
-- feature breakdown at the time of the event, so a later recheck diffs
-- against what was actually believed, not a re-derived memory of it.
CREATE TABLE IF NOT EXISTS position_thesis_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    event_type          TEXT NOT NULL,  -- 'entry' | 'scale_in' | 'recheck' | 'invalidated' | 'resolved'
    claim               TEXT,
    invalidation        TEXT,
    signals_snapshot    TEXT,  -- JSON, the reconcile() features dict
    verdict             TEXT,  -- 'intact' | 'weakening' | 'broken' | NULL
    note                TEXT,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_thesis_log_ticker_ts ON position_thesis_log(ticker, created_at);

-- watchlist_candidates: replaces strategies/watchlist.md's ## Candidates
-- section. note holds Stan's own reasoning text (why it's on the list,
-- signal read), same role watchlist.md's inline note played.
-- sentiment/news_headline (2026-08-01) carry the discovery pool's news
-- read through to the watchlist so a tick doesn't have to re-derive it.
--
-- Evaluation ordering and staleness-drop read DIFFERENT columns on
-- purpose (2026-08-01). They used to share idle_ticks, which deadlocked:
-- get_watchlist_batch() took the highest idle_ticks, mark-evaluated left
-- those flat, so an evaluated candidate froze one tick below the drop
-- threshold and held the top of the ordering forever, while every
-- candidate that hadn't been evaluated yet had to climb PAST it to get a
-- turn -- and reached the drop threshold first, so drop-stale deleted it
-- before it was ever looked at. Confirmed live: 4 candidates pinned at
-- idle_ticks 23 against a threshold of 24, re-evaluated for hours, while
-- names added the same afternoon were deleted unevaluated.
--   last_evaluated_at -> ordering (least-recently-evaluated first)
--   eval_count/added_at -> staleness (exhausted, or never converted)
--   idle_ticks -> diagnostic only, nothing decides on it anymore
CREATE TABLE IF NOT EXISTS watchlist_candidates (
    ticker            TEXT PRIMARY KEY,
    price             REAL,
    rsi               REAL,
    volume_ratio      REAL,
    macd_hist         REAL,
    sentiment         REAL,
    news_headline     TEXT,
    sector            TEXT,
    industry          TEXT,
    market_cap        REAL,
    ma_filing_flag    TEXT,
    idle_ticks        INTEGER NOT NULL DEFAULT 0,
    last_evaluated_at TEXT,
    eval_count        INTEGER NOT NULL DEFAULT 0,
    interest_score        INTEGER NOT NULL DEFAULT 0,
    last_researched_at    TEXT,
    research_confidence   REAL,
    research_note         TEXT,
    tree_match_state      TEXT,
    source            TEXT,
    note              TEXT,
    added_at          TEXT NOT NULL,
    last_touched_at   TEXT NOT NULL
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
    lifetime_win_pnl_sum    REAL NOT NULL DEFAULT 0.0,
    lifetime_loss_pnl_sum   REAL NOT NULL DEFAULT 0.0,
    ceiling_pct             REAL NOT NULL DEFAULT 0.067,
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

-- backtest_closed_trades (2026-08-02): export target for
-- scripts/export_backtest_trades.py, one row per closed position copied
-- out of a completed backtest_session.py session's own isolated DB.
-- Deliberately its OWN table, not a write into `positions` -- `positions`
-- has `ticker TEXT PRIMARY KEY`, so a backtest AAPL close would collide
-- with a live open/closed AAPL row (or a different session's own AAPL
-- close). The UNIQUE constraint below is what makes re-running the
-- export script after every completed day safe -- INSERT OR IGNORE just
-- no-ops on a trade already exported instead of erroring or duplicating.
-- paper-trading-rebuild's ml_trainer_service.py's _stan_closed_trades()
-- UNIONs this table with live `positions` closed rows -- it does NOT
-- read training_examples, so this is the actual integration point real
-- training reps flow through, not that table.
CREATE TABLE IF NOT EXISTS backtest_closed_trades (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id           TEXT NOT NULL,
    ticker               TEXT NOT NULL,
    entry_time           TEXT NOT NULL,
    closed_at            TEXT NOT NULL,
    realized_pnl         REAL,
    realized_return_pct  REAL,
    sector               TEXT,
    thesis               TEXT,
    close_reason         TEXT,
    exported_at          TEXT NOT NULL,
    UNIQUE(session_id, ticker, entry_time, closed_at)
);
CREATE INDEX IF NOT EXISTS idx_backtest_closed_trades_session ON backtest_closed_trades(session_id);
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
    _migrate_add_column(conn, "bankroll_state", "ceiling_pct", "REAL NOT NULL DEFAULT 0.067")
    # 2026-08-03: magnitude-weighted ceiling growth (bankroll.py
    # recalc_ceiling/_lifetime_payoff_ratio) needs avg win/loss $, not just
    # counts -- these two running sums back that.
    _migrate_add_column(conn, "bankroll_state", "lifetime_win_pnl_sum", "REAL NOT NULL DEFAULT 0.0")
    _migrate_add_column(conn, "bankroll_state", "lifetime_loss_pnl_sum", "REAL NOT NULL DEFAULT 0.0")
    # 2026-07-30: long-play support -- play_type distinguishes a small,
    # short-horizon "predicted up by predicted_by_date" bet from a normal
    # position; predicted_by_date/prediction_reason are NULL for standard
    # positions. See skills/long-play.md (or params.json risk.long_play)
    # for the mechanism this backs.
    _migrate_add_column(conn, "positions", "play_type", "TEXT NOT NULL DEFAULT 'standard'")
    _migrate_add_column(conn, "positions", "predicted_by_date", "TEXT")
    _migrate_add_column(conn, "positions", "prediction_reason", "TEXT")
    # 2026-08-01: discovery-pool signal plumbing -- promote_candidates.py
    # now carries the pool's news read (sentiment score + the confirming
    # headline) onto the candidate instead of dropping it, so a tick reads
    # it instead of re-deriving it. NULL on every pre-existing row.
    _migrate_add_column(conn, "watchlist_candidates", "sentiment", "REAL")
    _migrate_add_column(conn, "watchlist_candidates", "news_headline", "TEXT")
    # 2026-08-01: split the rotation/staleness deadlock (see the table
    # comment above). Existing rows migrate to last_evaluated_at NULL and
    # eval_count 0, i.e. "never evaluated" -- which is what puts them at
    # the FRONT of the new ordering, so the candidates the old scheme was
    # starving get looked at first.
    _migrate_add_column(conn, "watchlist_candidates", "last_evaluated_at", "TEXT")
    _migrate_add_column(conn, "watchlist_candidates", "eval_count", "INTEGER NOT NULL DEFAULT 0")
    # 2026-08-01: outcome-labeling correlation -- see the training_examples
    # CREATE TABLE comment above. Deliberately nullable with no default:
    # a NULL example_type means "pre-migration row, type unknown", which is
    # a state find_entry_training_example() has to treat differently from a
    # row that is known not to be an entry.
    _migrate_add_column(conn, "training_examples", "example_type", "TEXT")
    _migrate_add_column(conn, "training_examples", "position_entry_time", "TEXT")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_training_examples_entry_lookup
                    ON training_examples(ticker, example_type, label_win)""")
    # 2026-08-03: thesis persistence for conviction/long plays -- extends
    # the existing thesis/prediction_reason columns rather than replacing
    # them. thesis_claim/thesis_invalidation separate "why I believe this"
    # from "what would prove me wrong" (a different, more falsifiable
    # question than prediction_reason answers); thesis_entry_signals snapshots
    # the reconcile() breakdown at entry so a later recheck has something
    # concrete to diff against. thesis_last_checked_at/thesis_check_count
    # gate the daily re-confirmation cadence (see position_thesis_log above).
    # All nullable -- NULL for standard positions and every pre-existing row.
    _migrate_add_column(conn, "positions", "thesis_claim", "TEXT")
    _migrate_add_column(conn, "positions", "thesis_invalidation", "TEXT")
    _migrate_add_column(conn, "positions", "thesis_entry_signals", "TEXT")
    _migrate_add_column(conn, "positions", "thesis_last_checked_at", "TEXT")
    _migrate_add_column(conn, "positions", "thesis_check_count", "INTEGER NOT NULL DEFAULT 0")
    # 2026-08-12: pipeline attribution -- which upstream path (discovery
    # pool, discoveries/*.md, manual/gestalt) sourced the candidate that
    # became this decision. Previously only inferable after the fact from
    # active.md prose, not queryable. NULL for every pre-existing row and
    # for SELLs (a position's original source isn't re-derived at exit).
    _migrate_add_column(conn, "decisions", "source", "TEXT")
    # 2026-08-13: sector/industry/market_cap carried from discovery_pool.db's
    # candidates table (see discovery_db.py's record_fundamentals()) through
    # promote_candidates.py -> merge_discoveries.py onto the watchlist row,
    # so a BUY can auto-populate positions.sector without an explicit
    # --sector flag. NULL for every pre-existing row and for any candidate
    # whose enrichment fetch failed/was skipped.
    _migrate_add_column(conn, "watchlist_candidates", "sector", "TEXT")
    _migrate_add_column(conn, "watchlist_candidates", "industry", "TEXT")
    _migrate_add_column(conn, "watchlist_candidates", "market_cap", "REAL")
    # 2026-08-13: BWMN data-gap fix -- carries discovery_db.py's
    # ma_filing_flag (free SEC EDGAR check, scripts/edgar_scan.py) through
    # to the watchlist as informational context, same non-load-bearing
    # posture as sentiment/news_headline. No separate checked_at column
    # here -- that cadence state only needs to live in the pool
    # (discovery_pool.db), not duplicated onto every downstream copy.
    _migrate_add_column(conn, "watchlist_candidates", "ma_filing_flag", "TEXT")
    # 2026-08-13: resurrects wiki syntheses/watchlist-interest-scoring-system.md's
    # design (documented 2026-07-29, never wired in) -- see
    # compute_interest_score(). last_researched_at/research_confidence/
    # research_note are the research-escalation pipeline's output slots
    # (scripts/research_escalation.py) -- NULL until a candidate actually
    # gets a sessions_send exchange with the researcher agent.
    _migrate_add_column(conn, "watchlist_candidates", "interest_score", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column(conn, "watchlist_candidates", "last_researched_at", "TEXT")
    _migrate_add_column(conn, "watchlist_candidates", "research_confidence", "REAL")
    _migrate_add_column(conn, "watchlist_candidates", "research_note", "TEXT")
    # 2026-08-13: decision_heuristics.md tree-match outcome for this
    # candidate's most recent evaluation -- only an LLM turn (Stan's tick,
    # not a bare script) can actually judge a tree match against prose
    # trigger conditions, so this is set from tick_prompt.md step 8 via
    # mark_candidates_evaluated()'s tree_match_state param, not computed
    # here. scripts/research_escalation.py's selection query reads it.
    # One of 'active', 'watch', 'insufficient_data', 'no_match', or NULL
    # (never evaluated against the tree).
    _migrate_add_column(conn, "watchlist_candidates", "tree_match_state", "TEXT")
    conn.commit()


_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 2026-07-30: widened to allow a quoted string DEFAULT (e.g. DEFAULT
# 'standard') -- a normal, legitimate coltype fragment (play_type TEXT
# column) that the original charset rejected outright. Still anchored
# full-match against a fixed charset with no ';', '--', or '/*', so this
# doesn't reopen the injection risk the original allowlist closed.
_SQL_COLTYPE_RE = re.compile(r"^[A-Za-z0-9_ ().+'-]+$")


def _migrate_add_column(conn: sqlite3.Connection, table: str, column: str, coltype_and_default: str) -> None:
    """First ALTER-TABLE-style migration in this file (2026-07-28) -- no
    precedent to follow yet. CREATE TABLE IF NOT EXISTS above only creates
    the column on a fresh DB; an already-existing live DB (real trading
    state, not a test fixture) needs this to actually gain the column.
    Idempotent: checks PRAGMA table_info first since SQLite has no ADD
    COLUMN IF NOT EXISTS, and ALTER TABLE ADD COLUMN on an already-migrated
    DB would raise 'duplicate column name'.

    table/column/coltype_and_default are interpolated directly into SQL --
    unavoidable for identifiers (SQLite, like every SQL engine, has no way
    to bind a table/column name as a query parameter), so this validates
    each against a strict allowlist first and raises rather than execute
    anything that doesn't match a plain identifier / type-and-constraint
    fragment. The only caller today (line ~174) passes hardcoded literals,
    so this is defense-in-depth against a future caller passing anything
    externally influenced, not a fix for an active exploit.
    """
    for label, value in (("table", table), ("column", column)):
        if not _SQL_IDENTIFIER_RE.match(value):
            raise ValueError(f"_migrate_add_column: unsafe {label} name {value!r}")
    if not _SQL_COLTYPE_RE.match(coltype_and_default):
        raise ValueError(f"_migrate_add_column: unsafe coltype_and_default {coltype_and_default!r}")

    existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_and_default}")


def insert_decision(conn: sqlite3.Connection, ticker: str, timestamp: str, decision: str,
                     conviction: float = None, rationale: str = "", regime: str = None,
                     decision_json: str = None, source: str = None) -> int:
    with conn:
        cur = conn.execute(
            """INSERT INTO decisions (ticker, timestamp, decision, conviction, rationale, regime, decision_json, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, timestamp, decision, conviction, rationale, regime, decision_json, source),
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
                             label_horizon: str = None, created_at: str = None,
                             example_type: str = None, position_entry_time: str = None) -> int:
    """example_type: 'entry' (a BUY -- the row that carries the predictive
    signals and is the only kind eligible for outcome labeling), 'exit' (a
    SELL's own decision log), or 'observation' (HOLD). position_entry_time
    is the positions.entry_time of the position this row was opened for --
    the correlation key record_trade_close() uses to label the right row."""
    with conn:
        cur = conn.execute(
            """INSERT INTO training_examples
               (ticker, decision_id, trade_id, label_win, label_return_pct, label_horizon, features,
                created_at, example_type, position_entry_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, decision_id, trade_id, label_win, label_return_pct, label_horizon, features,
             created_at, example_type, position_entry_time),
        )
        return cur.lastrowid


def update_training_example_features(conn: sqlite3.Connection, training_example_id: int, features: str,
                                      decision_id: int = None, position_entry_time: str = None) -> None:
    """Merge richer data into an already-written row instead of inserting a
    second one for the same position (2026-08-01). executor.py writes an
    entry row deterministically the moment a BUY fills; if the agent later
    logs the same trade via record_decision.py with real scored signals,
    that has to land on the SAME row -- two rows for one position would
    reintroduce exactly the "which row does the close label?" ambiguity
    this whole mechanism exists to remove.

    decision_id/position_entry_time are only overwritten when a non-NULL
    value is passed (COALESCE), so a later merge can add a link but never
    erase one."""
    with conn:
        conn.execute(
            """UPDATE training_examples
               SET features = ?,
                   decision_id = COALESCE(?, decision_id),
                   position_entry_time = COALESCE(?, position_entry_time)
               WHERE id = ?""",
            (features, decision_id, position_entry_time, training_example_id),
        )


def label_training_example(conn: sqlite3.Connection, training_example_id: int, trade_id: str,
                            label_win: int, label_return_pct: float,
                            label_horizon: str = "trade_close") -> None:
    """label_horizon defaults to 'trade_close' (the original/only caller,
    decisions.py's record_trade_close). 2026-07-30: also used with
    label_horizon='long_play_prediction' when a long play resolves at its
    predicted_by_date -- a distinct label from an actual realized trade
    close, queryable separately (see scripts/signal_scorecard.py-style
    per-label-horizon hit-rate reporting)."""
    with conn:
        conn.execute(
            """UPDATE training_examples
               SET trade_id = ?, label_win = ?, label_return_pct = ?, label_horizon = ?
               WHERE id = ?""",
            (trade_id, label_win, label_return_pct, label_horizon, training_example_id),
        )


def latest_unlabeled_training_example(conn: sqlite3.Connection, ticker: str):
    """DEPRECATED for outcome labeling -- kept only for callers that
    genuinely want "newest unlabeled row, any type".

    Do not use this to attach a win/loss label: on a SELL the newest
    unlabeled row for the ticker is the SELL's own decision row (features
    like {"stop_trigger": ...}), not the BUY row carrying the signals the
    outcome is supposed to validate. Confirmed in the live DB 2026-08-01:
    BUY rows sat permanently label_win=NULL while SELL rows got labeled in
    their place, which is why the signal scorecard learned nothing. Use
    find_entry_training_example() instead."""
    row = conn.execute(
        """SELECT id FROM training_examples
           WHERE ticker = ? AND label_win IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        (ticker,),
    ).fetchone()
    return row["id"] if row else None


def find_entry_training_example(conn: sqlite3.Connection, ticker: str, position_entry_time: str = None):
    """The open ENTRY row for `ticker` -- the row a close should label.

    Two tiers, most specific first:
      1. exact position link: an unlabeled row whose position_entry_time
         matches the position being closed (written by executor.py at BUY
         fill time),
      2. newest unlabeled example_type='entry' row for the ticker -- covers
         a row written before the position link existed, or a re-entry
         whose entry_time drifted (Alpaca's avg_entry_price/entry_time can
         move on a scale-in).

    Never falls back to "newest unlabeled row of any type" -- that was the
    bug. Pre-migration rows (example_type IS NULL) are handled separately
    by the caller, see decisions.record_trade_close(). Returns a dict or
    None."""
    if position_entry_time:
        row = conn.execute(
            """SELECT * FROM training_examples
               WHERE ticker = ? AND label_win IS NULL AND position_entry_time = ?
               ORDER BY created_at DESC LIMIT 1""",
            (ticker, position_entry_time),
        ).fetchone()
        if row:
            return dict(row)
    row = conn.execute(
        """SELECT * FROM training_examples
           WHERE ticker = ? AND label_win IS NULL AND example_type = 'entry'
           ORDER BY created_at DESC LIMIT 1""",
        (ticker,),
    ).fetchone()
    return dict(row) if row else None


def unlabeled_legacy_training_examples(conn: sqlite3.Connection, ticker: str) -> list:
    """Unlabeled rows written before example_type existed (2026-08-01), newest
    first. These can be a BUY row, a SELL row or a HOLD row -- indistinguishable
    from the column alone, so the caller decides by inspecting features (see
    decisions.record_trade_close's legacy tier)."""
    rows = conn.execute(
        """SELECT * FROM training_examples
           WHERE ticker = ? AND label_win IS NULL AND example_type IS NULL
           ORDER BY created_at DESC""",
        (ticker,),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_labeled_training_examples(conn: sqlite3.Connection, label_horizon: str = None) -> list:
    """label_horizon filter added 2026-07-30: 'trade_close' (a real closed
    trade's win/loss) and 'long_play_prediction' (whether a long play's
    predicted_by_date guess was right, independent of whether the position
    was later sold) are different kinds of labels and must not be silently
    aggregated together -- unfiltered (default) mixes both, matching the
    original pre-long-play behavior for any existing caller."""
    if label_horizon:
        rows = conn.execute(
            "SELECT features, label_win FROM training_examples WHERE label_win IS NOT NULL AND label_horizon = ?",
            (label_horizon,),
        ).fetchall()
    else:
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
                     entry_time: str, sector: str = None, thesis: str = None, now: str = None,
                     play_type: str = "standard", predicted_by_date: str = None,
                     prediction_reason: str = None, thesis_claim: str = None,
                     thesis_invalidation: str = None, thesis_entry_signals: str = None) -> None:
    """Open a new position or update an existing one's thesis/shares (e.g.
    a scale-in). Does not touch status/close fields.

    play_type/predicted_by_date/prediction_reason back the long-play
    mechanism (2026-07-30, params.json risk.long_play): a small, short-
    horizon "predicted up by predicted_by_date" bet, distinct from a
    normal ('standard') position. Set once at initial entry -- deliberately
    NOT in the ON CONFLICT SET clause below, so a later scale-in can never
    change a position's play_type after the fact, same principle as
    sector/thesis being COALESCEd rather than blindly overwritten.

    thesis_claim/thesis_invalidation/thesis_entry_signals (2026-08-03) are
    COALESCEd like thesis/sector, not set-once like prediction_reason -- they
    represent Stan's *current* read, which a scale-in can legitimately
    update. Unlike the old bare `thesis` column, this doesn't lose history:
    every entry/scale-in should also get its own position_thesis_log row
    (see log_thesis_event) so the prior claim isn't gone, just superseded."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO positions (ticker, shares, entry_price, entry_time, sector, thesis, status, updated_at,
                                       play_type, predicted_by_date, prediction_reason,
                                       thesis_claim, thesis_invalidation, thesis_entry_signals)
               VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                   shares = excluded.shares,
                   sector = COALESCE(excluded.sector, positions.sector),
                   thesis = COALESCE(excluded.thesis, positions.thesis),
                   thesis_claim = COALESCE(excluded.thesis_claim, positions.thesis_claim),
                   thesis_invalidation = COALESCE(excluded.thesis_invalidation, positions.thesis_invalidation),
                   thesis_entry_signals = COALESCE(excluded.thesis_entry_signals, positions.thesis_entry_signals),
                   updated_at = excluded.updated_at""",
            (ticker, shares, entry_price, entry_time, sector, thesis, now,
             play_type, predicted_by_date, prediction_reason,
             thesis_claim, thesis_invalidation, thesis_entry_signals),
        )


def log_thesis_event(conn: sqlite3.Connection, ticker: str, event_type: str, claim: str = None,
                      invalidation: str = None, signals_snapshot: str = None, verdict: str = None,
                      note: str = None, now: str = None) -> int:
    """Append-only write to position_thesis_log -- the history upsert_position's
    COALESCE-on-current-state can't provide by itself. Call this alongside
    every upsert_position('entry'/'scale_in') and every daily recheck
    ('recheck', with verdict set)."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        cur = conn.execute(
            """INSERT INTO position_thesis_log
                   (ticker, event_type, claim, invalidation, signals_snapshot, verdict, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, event_type, claim, invalidation, signals_snapshot, verdict, note, now),
        )
        return cur.lastrowid


def get_thesis_log(conn: sqlite3.Connection, ticker: str, limit: int = 20) -> list:
    """Most-recent-first thesis history for one ticker."""
    rows = conn.execute(
        """SELECT * FROM position_thesis_log WHERE ticker = ?
           ORDER BY created_at DESC, id DESC LIMIT ?""",
        (ticker, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def update_thesis_check(conn: sqlite3.Connection, ticker: str, checked_at: str) -> None:
    """Bump thesis_last_checked_at/thesis_check_count after a daily
    recheck -- separate from log_thesis_event so a caller can update the
    cadence-gating state and append the log row as two explicit steps."""
    with conn:
        conn.execute(
            """UPDATE positions SET thesis_last_checked_at = ?,
                   thesis_check_count = thesis_check_count + 1
               WHERE ticker = ?""",
            (checked_at, ticker),
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


def resolve_long_play(conn: sqlite3.Connection, ticker: str, updated_at: str) -> None:
    """Called once, mechanically, by executor.py's check_stops() when a
    long play's predicted_by_date arrives (2026-07-30, params.json
    risk.long_play). Flips play_type back to 'standard' so the position
    reverts to the normal trailing-stop schedule and this doesn't re-fire
    every subsequent tick -- predicted_by_date/prediction_reason are
    deliberately left in place as a permanent audit trail of what was
    predicted, not cleared."""
    with conn:
        conn.execute(
            "UPDATE positions SET play_type = 'standard', updated_at = ? WHERE ticker = ?",
            (updated_at, ticker),
        )


def get_open_positions(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT * FROM positions WHERE status = 'open' ORDER BY entry_time").fetchall()
    return [dict(r) for r in rows]


def compute_sector_performance(conn: sqlite3.Connection, lookback_days: int = 30, min_trades: int = 8,
                                now: str = None) -> dict:
    """2026-08-13: aggregates live closed positions by sector into
    {sector: {trades, wins, win_rate, avg_return_pct}} -- feeds
    discovery_db.py's sector-performance-weighted ranking term, so
    discovery naturally favors whatever sector is actually performing
    right now, continuously, with no hardcoded sector/ticker names
    anywhere. See discovery_daemon.py's maybe_refresh_sector_performance().

    Sectors with fewer than min_trades closed positions in the lookback
    window are omitted entirely, not included with a noisy win_rate --
    the exact problem with reacting to a 1-2 trade sample (see the
    2026-08-13 diagnosis this was built from: two Technology losses in one
    session isn't evidence of anything on its own). Uses realized_pnl > 0
    for win/loss (matches how tasks/pending.md's own sector-erosion
    tracking already frames "win rate"), and avg_return_pct as a secondary
    magnitude signal, stored but not yet used in ranking (future
    refinement -- win_rate alone is what's actually being tracked/
    discussed today, keeping this consistent with that)."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    cutoff = (datetime.datetime.fromisoformat(now) - datetime.timedelta(days=lookback_days)).isoformat()

    rows = conn.execute(
        """SELECT sector, realized_pnl, realized_return_pct FROM positions
           WHERE status = 'closed' AND sector IS NOT NULL AND closed_at >= ?""",
        (cutoff,),
    ).fetchall()

    by_sector: dict = {}
    for r in rows:
        bucket = by_sector.setdefault(r["sector"], {"trades": 0, "wins": 0, "return_sum": 0.0, "return_count": 0})
        bucket["trades"] += 1
        if r["realized_pnl"] is not None and r["realized_pnl"] > 0:
            bucket["wins"] += 1
        if r["realized_return_pct"] is not None:
            bucket["return_sum"] += r["realized_return_pct"]
            bucket["return_count"] += 1

    result = {}
    for sector, bucket in by_sector.items():
        if bucket["trades"] < min_trades:
            continue
        result[sector] = {
            "trades": bucket["trades"],
            "wins": bucket["wins"],
            "win_rate": bucket["wins"] / bucket["trades"],
            "avg_return_pct": (bucket["return_sum"] / bucket["return_count"]) if bucket["return_count"] else None,
        }
    return result


def get_all_positions(conn: sqlite3.Connection) -> list:
    """Open + closed, no status filter -- used by merge_discoveries.py's
    dedup (a closed ticker shouldn't be re-added as a fresh candidate
    either) and by trader_query.py's closed-position lookup."""
    rows = conn.execute("SELECT * FROM positions ORDER BY entry_time").fetchall()
    return [dict(r) for r in rows]


def get_position(conn: sqlite3.Connection, ticker: str):
    row = conn.execute("SELECT * FROM positions WHERE ticker = ?", (ticker,)).fetchone()
    return dict(row) if row else None


# ── Backtest trade export (scripts/export_backtest_trades.py) ───────────

def get_closed_trades_for_export(conn: sqlite3.Connection) -> list:
    """Closed positions from a backtest session's own DB, filtered the
    same way ml_trainer_service.py's _stan_closed_trades() filters live
    positions -- realized_return_pct populated and entry_time != closed_at
    (excludes the pre-2026-07-28 migration rows with fabricated
    timestamps upstream; a fresh backtest session can't produce one of
    those, but keeping the same filter here means both sources stay
    comparable). conn should be opened against the session's db_path, not
    the live DB."""
    rows = conn.execute(
        """SELECT ticker, entry_time, closed_at, realized_pnl, realized_return_pct, sector, thesis, close_reason
           FROM positions
           WHERE status = 'closed' AND realized_return_pct IS NOT NULL AND entry_time != closed_at"""
    ).fetchall()
    return [dict(r) for r in rows]


def export_closed_trade(conn: sqlite3.Connection, session_id: str, ticker: str, entry_time: str,
                         closed_at: str, realized_pnl: float, realized_return_pct: float,
                         sector: str = None, thesis: str = None, close_reason: str = None,
                         now: str = None) -> bool:
    """Writes one closed backtest trade into the LIVE db's
    backtest_closed_trades table (conn should be opened against the live
    trader.db, not the session's own file). INSERT OR IGNORE against the
    (session_id, ticker, entry_time, closed_at) UNIQUE constraint makes
    this safe to call repeatedly for the same session -- returns False
    (no-op) on an already-exported trade, True if it actually inserted a
    new row, so the caller can report real vs. skipped-duplicate counts."""
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO backtest_closed_trades
                   (session_id, ticker, entry_time, closed_at, realized_pnl, realized_return_pct,
                    sector, thesis, close_reason, exported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, ticker, entry_time, closed_at, realized_pnl, realized_return_pct,
             sector, thesis, close_reason, now),
        )
    return cur.rowcount > 0


# ── Watchlist candidates ─────────────────────────────────────────────────

def upsert_watchlist_candidate(conn: sqlite3.Connection, ticker: str, price: float = None,
                                rsi: float = None, volume_ratio: float = None, macd_hist: float = None,
                                sentiment: float = None, news_headline: str = None,
                                sector: str = None, industry: str = None, market_cap: float = None,
                                ma_filing_flag: str = None,
                                source: str = None, note: str = None, now: str = None) -> None:
    """Adds a new candidate or touches an existing one -- idle_ticks resets
    to 0 on touch, matching watchlist.md's existing convention.

    sentiment/news_headline/sector/industry/market_cap COALESCE on update
    (like source/note) rather than overwriting like the technicals do:
    they're descriptive context with their own cadence (fetched once at
    discovery time, see discovery_db.record_fundamentals()), so a plain
    technical refresh shouldn't blank the enrichment read that got the
    candidate promoted in the first place.

    Deliberately does not touch last_evaluated_at/eval_count: being
    re-discovered or re-priced is not the same as being evaluated, and
    letting a touch reset either one would hand a frequently-rediscovered
    candidate a permanent front-of-queue slot -- a fresh instance of the
    deadlock those columns exist to fix. New rows get NULL/0 from the
    schema defaults; existing rows keep whatever they had."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO watchlist_candidates
                   (ticker, price, rsi, volume_ratio, macd_hist, sentiment, news_headline,
                    sector, industry, market_cap, ma_filing_flag,
                    idle_ticks, source, note, added_at, last_touched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                   price = excluded.price,
                   rsi = excluded.rsi,
                   volume_ratio = excluded.volume_ratio,
                   macd_hist = excluded.macd_hist,
                   sentiment = COALESCE(excluded.sentiment, watchlist_candidates.sentiment),
                   news_headline = COALESCE(excluded.news_headline, watchlist_candidates.news_headline),
                   sector = COALESCE(excluded.sector, watchlist_candidates.sector),
                   industry = COALESCE(excluded.industry, watchlist_candidates.industry),
                   market_cap = COALESCE(excluded.market_cap, watchlist_candidates.market_cap),
                   ma_filing_flag = COALESCE(excluded.ma_filing_flag, watchlist_candidates.ma_filing_flag),
                   idle_ticks = 0,
                   source = COALESCE(excluded.source, watchlist_candidates.source),
                   note = COALESCE(excluded.note, watchlist_candidates.note),
                   last_touched_at = excluded.last_touched_at""",
            (ticker, price, rsi, volume_ratio, macd_hist, sentiment, news_headline,
             sector, industry, market_cap, ma_filing_flag, source, note, now, now),
        )


def increment_idle_ticks(conn: sqlite3.Connection, except_tickers: list = None) -> None:
    """Bumps idle_ticks for every candidate not explicitly touched this
    tick. As of 2026-08-01 idle_ticks is a DIAGNOSTIC ONLY -- "ticks since
    last evaluated", useful when eyeballing the table -- and nothing
    orders or drops on it. Prefer mark_candidates_evaluated(), which
    maintains it alongside the columns that actually decide things."""
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


TREE_MATCH_STATES = {"active", "watch", "insufficient_data", "no_match"}


def mark_candidates_evaluated(conn: sqlite3.Connection, tickers: list, now: str = None,
                               entry_signal_tickers: list = None, interest_weights: dict = None,
                               tree_match_state: dict = None) -> None:
    """Records that `tickers` were evaluated this tick: stamps
    last_evaluated_at (what get_watchlist_batch orders on), bumps
    eval_count (what drop_stale_watchlist_candidates retires on), and
    zeroes their idle_ticks while everyone else's climbs.

    Also recomputes and persists interest_score for each evaluated ticker
    (2026-08-13) -- this is the natural per-tick touch point, since it's
    already "this candidate was just looked at with current signals".
    entry_signal_tickers (subset of tickers) marks which ones matched a
    decision_heuristics.md Active node this eval -- the one interest
    signal compute_interest_score() can't derive from the row itself.

    tree_match_state (2026-08-13, {ticker: state}, state in
    TREE_MATCH_STATES) records the tree-match OUTCOME for
    scripts/research_escalation.py's selection query -- distinct from
    entry_signal_tickers, which only flags Active-node ENTRY matches (an
    interest_score signal). A ticker not present in tree_match_state keeps
    its prior value (most candidates aren't evaluated every tick, same as
    every other column here) -- pass an explicit state to update it, don't
    rely on a default.

    One transaction so a crash between the two halves can't leave a batch
    stamped-but-not-counted."""
    import datetime
    tickers = [t.upper() for t in (tickers or [])]
    entry_signal_tickers = {t.upper() for t in (entry_signal_tickers or [])}
    tree_match_state = {t.upper(): s for t, s in (tree_match_state or {}).items()}
    for state in tree_match_state.values():
        if state not in TREE_MATCH_STATES:
            raise ValueError(f"invalid tree_match_state {state!r}, must be one of {TREE_MATCH_STATES}")
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        if tickers:
            placeholders = ",".join("?" * len(tickers))
            conn.execute(
                f"""UPDATE watchlist_candidates
                       SET last_evaluated_at = ?, eval_count = eval_count + 1, idle_ticks = 0
                     WHERE ticker IN ({placeholders})""",
                [now, *tickers],
            )
            conn.execute(
                f"UPDATE watchlist_candidates SET idle_ticks = idle_ticks + 1 WHERE ticker NOT IN ({placeholders})",
                tickers,
            )
            for ticker in tickers:
                row = conn.execute(
                    "SELECT * FROM watchlist_candidates WHERE ticker = ?", (ticker,)
                ).fetchone()
                if row is None:
                    continue
                score = compute_interest_score(
                    dict(row), entry_signal_triggered=ticker in entry_signal_tickers,
                    weights=interest_weights,
                )
                conn.execute(
                    "UPDATE watchlist_candidates SET interest_score = ? WHERE ticker = ?",
                    (score, ticker),
                )
                if ticker in tree_match_state:
                    conn.execute(
                        "UPDATE watchlist_candidates SET tree_match_state = ? WHERE ticker = ?",
                        (tree_match_state[ticker], ticker),
                    )
        else:
            conn.execute("UPDATE watchlist_candidates SET idle_ticks = idle_ticks + 1")


DEFAULT_INTEREST_SCORE_WEIGHTS = {
    "entry_signal_triggered": 2,
    "sentiment_positive": 2,
    "unusual_options_flow": 1,
    "volume_gt_2x_avg": 1,
    "rsi_near_entry_zone": 1,
    "idle_decay_per_12_ticks": -1,
}
RSI_ENTRY_ZONE = (30.0, 45.0)  # matches wiki syntheses/watchlist-interest-scoring-system.md's stated band
VOLUME_RATIO_THRESHOLD = 2.0
DEFAULT_INTEREST_MIN_SCORE = 2


def compute_interest_score(candidate_row: dict, entry_signal_triggered: bool = False,
                            weights: dict = None) -> int:
    """Pure function -- no DB access, no side effects. Resurrects (and
    slightly revises) wiki syntheses/watchlist-interest-scoring-system.md's
    design, documented 2026-07-29 but never actually wired in.

    Signals derived from the row itself: sentiment_positive (sentiment >
    0), volume_gt_2x_avg (volume_ratio >= 2.0), rsi_near_entry_zone (rsi in
    RSI_ENTRY_ZONE). entry_signal_triggered can only come from the caller
    -- it means a decision_heuristics.md Active node matched this
    evaluation, which trader_db.py has no way to know on its own.
    unusual_options_flow is accepted for forward-compatibility but has no
    live data source yet (tick_prompt.md step 8: get_flow is disabled, not
    real options data) -- always contributes 0 today.

    idle_decay applies per eval_count // 12, NOT idle_ticks -- idle_ticks
    resets on every technical touch (see upsert_watchlist_candidate) and
    is diagnostic-only since the 2026-08-01 rotation-deadlock fix (see the
    watchlist_candidates schema comment), so it wouldn't accumulate
    meaningfully as an "idle" signal. eval_count -- "looked at this many
    times" -- is the schema's own designated staleness column, a better
    fit for what the original design meant by decay."""
    w = weights or DEFAULT_INTEREST_SCORE_WEIGHTS
    sentiment = candidate_row.get("sentiment")
    volume_ratio = candidate_row.get("volume_ratio")
    rsi = candidate_row.get("rsi")
    eval_count = candidate_row.get("eval_count") or 0

    score = 0
    if entry_signal_triggered:
        score += w.get("entry_signal_triggered", 0)
    if sentiment is not None and sentiment > 0:
        score += w.get("sentiment_positive", 0)
    if volume_ratio is not None and volume_ratio >= VOLUME_RATIO_THRESHOLD:
        score += w.get("volume_gt_2x_avg", 0)
    if rsi is not None and RSI_ENTRY_ZONE[0] <= rsi <= RSI_ENTRY_ZONE[1]:
        score += w.get("rsi_near_entry_zone", 0)
    score += w.get("idle_decay_per_12_ticks", 0) * (eval_count // 12)
    return score


DEFAULT_MAX_EVALUATIONS_BEFORE_DROP = 12
DEFAULT_MAX_AGE_HOURS_BEFORE_DROP = 48


MIN_EVALUATIONS_FOR_INTEREST_DROP = 3


def drop_stale_watchlist_candidates(conn: sqlite3.Connection, max_evaluations: int = None,
                                     max_age_hours: float = None, interest_min_score: int = None,
                                     now: str = None) -> list:
    """Retires candidates on up to three signals, none of which is the one
    get_watchlist_batch() orders by (2026-08-01 -- sharing that signal is
    what deadlocked the rotation, see the schema comment):

      eval_count >= max_evaluations -- looked at this many times and still
        never worth a position. Exhausted, not neglected.
      added_at older than max_age_hours -- backstop for anything that
        somehow still isn't converting or being seen.
      interest_score < interest_min_score AND eval_count >=
        MIN_EVALUATIONS_FOR_INTEREST_DROP (2026-08-13) -- a faster exit for
        candidates that are demonstrably uninteresting (low score) rather
        than waiting out the full max_evaluations/max_age_hours backstop.
        The MIN_EVALUATIONS_FOR_INTEREST_DROP floor guards a brand-new
        candidate from being dropped on its first low-signal touch, before
        it's had a fair look -- same "exhausted, not neglected" posture as
        max_evaluations above, just a lower bar.

    Returns dropped tickers. All three thresholds are independently
    optional; passing none drops nothing rather than everything."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    clauses, params = [], []
    if max_evaluations is not None:
        clauses.append("eval_count >= ?")
        params.append(max_evaluations)
    if max_age_hours is not None:
        clauses.append("(julianday(?) - julianday(added_at)) * 24.0 >= ?")
        params.extend([now, max_age_hours])
    if interest_min_score is not None:
        clauses.append(f"(interest_score < ? AND eval_count >= {MIN_EVALUATIONS_FOR_INTEREST_DROP})")
        params.append(interest_min_score)
    if not clauses:
        return []

    where = " OR ".join(clauses)
    with conn:
        rows = conn.execute(f"SELECT ticker FROM watchlist_candidates WHERE {where}", params).fetchall()
        dropped = [r["ticker"] for r in rows]
        conn.execute(f"DELETE FROM watchlist_candidates WHERE {where}", params)
    return dropped


def get_watchlist_candidates(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT * FROM watchlist_candidates ORDER BY idle_ticks, added_at").fetchall()
    return [dict(r) for r in rows]


def get_watchlist_batch(conn: sqlite3.Connection, limit: int) -> list:
    """Least-recently-evaluated-first slice for bounded per-tick evaluation
    (2026-07-28, fixes stonks-tick blowing its cron timeout evaluating the
    full list every tick). Pair with mark_candidates_evaluated(conn,
    <this batch's tickers>) after evaluating.

    Never-evaluated candidates (last_evaluated_at IS NULL) sort first,
    oldest-added among them, so a name that just entered the watchlist
    gets seen promptly instead of having to out-wait the incumbents. After
    that it's a strict least-recently-evaluated round-robin, which is
    self-correcting: evaluating a candidate is exactly what sends it to
    the back of the queue.

    Ordering deliberately does NOT read idle_ticks or eval_count -- the
    columns drop_stale_watchlist_candidates() retires on. Sharing one
    counter between "who's next" and "who's expired" is what produced the
    2026-08-01 deadlock (see the schema comment)."""
    rows = conn.execute(
        """SELECT * FROM watchlist_candidates
            ORDER BY last_evaluated_at IS NOT NULL, last_evaluated_at ASC, added_at ASC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def remove_watchlist_candidate(conn: sqlite3.Connection, ticker: str) -> None:
    """Used when a candidate is promoted into an actual position."""
    with conn:
        conn.execute("DELETE FROM watchlist_candidates WHERE ticker = ?", (ticker,))


def record_watchlist_research(conn: sqlite3.Connection, ticker: str, confidence: float = None,
                               note: str = None, now: str = None) -> None:
    """Stamps the outcome of a research-escalation sessions_send exchange
    (2026-08-13, scripts/research_escalation.py) -- last_researched_at is
    what the escalation cooldown reads (get_research_escalation_candidates),
    confidence/note are the durable takeaway a live tick can weigh without
    re-running the exchange. No-ops on an unknown ticker (candidate may
    have already been dropped/promoted between selection and research)."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        conn.execute(
            """UPDATE watchlist_candidates
                  SET last_researched_at = ?, research_confidence = ?, research_note = ?
                WHERE ticker = ?""",
            (now, confidence, note, ticker.upper()),
        )


def get_research_escalation_candidates(conn: sqlite3.Connection, interest_min_score: int,
                                        cooldown_hours: float = None, cap: int = None,
                                        now: str = None) -> list:
    """Selection query for scripts/research_escalation.py -- pure SQL, no
    LLM judgment needed here (that already happened when Stan set
    tree_match_state during tick_prompt.md step 8's evaluation, via
    mark_candidates_evaluated).

    Trigger: tree_match_state indicates the decision tree did NOT cleanly
    resolve this candidate ('watch', 'insufficient_data', or 'no_match' --
    NOT 'active', which is exactly the case that doesn't need research)
    AND interest_score >= interest_min_score (not worth the cost on a
    candidate nobody's interested in) AND not researched within
    cooldown_hours (default: never researched, or long enough ago).

    Ordered highest-interest-first, capped at `cap` -- this is the
    expensive step (a real sessions_send LLM conversation per candidate),
    so bounding it matters more than for the cheaper enrichment cadences
    elsewhere in this pipeline."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    clauses = ["tree_match_state IN ('watch', 'insufficient_data', 'no_match')",
               "interest_score >= ?"]
    params = [interest_min_score]
    if cooldown_hours is not None:
        clauses.append(
            "(last_researched_at IS NULL OR (julianday(?) - julianday(last_researched_at)) * 24.0 >= ?)"
        )
        params.extend([now, cooldown_hours])
    where = " AND ".join(clauses)
    sql = f"""SELECT * FROM watchlist_candidates
               WHERE {where}
               ORDER BY interest_score DESC, added_at ASC"""
    if cap is not None:
        sql += " LIMIT ?"
        params.append(cap)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def most_recent_watchlist_source_touch(conn: sqlite3.Connection, source_prefix: str) -> str | None:
    """Most recent last_touched_at among watchlist_candidates whose source
    starts with source_prefix (e.g. "discovery_pool") -- lets a caller
    outside this DB (discovery_daemon.py's health check) tell whether the
    pool -> promote_candidates.py -> watchlist path is actually landing
    rows downstream, not just whether the daemon's own screening cycle is
    alive. Returns None if no matching row exists yet."""
    row = conn.execute(
        "SELECT MAX(last_touched_at) AS ts FROM watchlist_candidates WHERE source LIKE ?",
        (f"{source_prefix}%",),
    ).fetchone()
    return row["ts"] if row else None


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
                           lifetime_win_pnl_sum: float = 0.0, lifetime_loss_pnl_sum: float = 0.0,
                           ceiling_pct: float = None, now: str = None) -> None:
    """ceiling_pct defaults to None -- callers that don't know about the
    2026-07-28 equity-scaled-ceiling addition (e.g. bankroll.py's existing
    write_bankroll(), unchanged) must not silently reset an already-
    accumulated ceiling_pct back to the schema default on every write. The
    COALESCE in the ON CONFLICT branch preserves the stored value when None
    is passed; the VALUES-clause COALESCE only matters for the very first
    row (satisfies the NOT NULL constraint before any row exists)."""
    import datetime
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO bankroll_state
                   (id, ceiling, growth_rate, decay_rate, target_profit_pct, closed_trades_session,
                    wins_session, losses_session, net_pnl_session, total_deployed_session,
                    lifetime_trades, lifetime_net_pnl, lifetime_wins, lifetime_losses,
                    lifetime_win_pnl_sum, lifetime_loss_pnl_sum, ceiling_pct, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0.067), ?)
               ON CONFLICT(id) DO UPDATE SET
                   ceiling = excluded.ceiling, growth_rate = excluded.growth_rate,
                   decay_rate = excluded.decay_rate, target_profit_pct = excluded.target_profit_pct,
                   closed_trades_session = excluded.closed_trades_session, wins_session = excluded.wins_session,
                   losses_session = excluded.losses_session, net_pnl_session = excluded.net_pnl_session,
                   total_deployed_session = excluded.total_deployed_session,
                   lifetime_trades = excluded.lifetime_trades, lifetime_net_pnl = excluded.lifetime_net_pnl,
                   lifetime_wins = excluded.lifetime_wins, lifetime_losses = excluded.lifetime_losses,
                   lifetime_win_pnl_sum = excluded.lifetime_win_pnl_sum,
                   lifetime_loss_pnl_sum = excluded.lifetime_loss_pnl_sum,
                   ceiling_pct = COALESCE(?, bankroll_state.ceiling_pct),
                   updated_at = excluded.updated_at""",
            (ceiling, growth_rate, decay_rate, target_profit_pct, closed_trades_session, wins_session,
             losses_session, net_pnl_session, total_deployed_session, lifetime_trades, lifetime_net_pnl,
             lifetime_wins, lifetime_losses, lifetime_win_pnl_sum, lifetime_loss_pnl_sum,
             ceiling_pct, now, ceiling_pct),
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
