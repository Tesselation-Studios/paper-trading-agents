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


# ─────────────────────────────────────────────────────────────────────────────
# Canonical feature keys
#
# signal_scorecard.score_signals() tallies by EXACT key name, so the same
# underlying signal logged under three spellings is three separate tallies
# that each stay under the 10-sample significance threshold forever.
# Confirmed in the live DB 2026-08-01: `technical` (25 rows), `macd_hist` (2),
# `macdh` (2), `rsi` (4) and `volume` (16) / `vol_ratio` (1) were all being
# counted separately, and every signal in state/signal_scorecard.json was
# permanently "insufficient_data".
#
# Canonical set = the signal names skills/self-improving-agent.md tells the
# agent to score, which is the list it actually works from. Aliases are
# folded into their canonical name at WRITE time (and again at read time in
# signal_scorecard.py, so historical rows fold in too). An unrecognized
# signal-shaped key is kept -- never silently dropped, this is live trading
# data -- but warned about loudly so a new spelling can't fragment the
# tallies again unnoticed. Adding a genuinely new signal means adding it
# here on purpose.
# ─────────────────────────────────────────────────────────────────────────────

CANONICAL_SIGNAL_KEYS = frozenset({
    "technical",     # RSI / MACD / price structure
    "sentiment",     # FinBERT news sentiment
    "regime",        # get_market_regime
    "macro",         # get_macro
    "fundamentals",  # get_fundamentals
    "insiders",      # get_insiders
    "congress",      # get_congress
    "social",        # Reddit / StockTwits / Bluesky
    "momentum",      # cross-sectional rank
    "narrative",     # wiki synthesis pages
    "ml_signal",     # trained win/loss classifier
    "volume",        # relative volume / volume ratio
    "flow",          # options flow -- tool currently disabled, historical rows exist
})

# alias -> canonical. Every spelling ever seen in the live DB plus the
# obvious near-misses of the canonical names.
SIGNAL_KEY_ALIASES = {
    "rsi": "technical",
    "macd": "technical",
    "macdh": "technical",
    "macd_hist": "technical",
    "macd_histogram": "technical",
    "technicals": "technical",
    "price_action": "technical",
    "vol_ratio": "volume",
    "volume_ratio": "volume",
    "rel_volume": "volume",
    "relative_volume": "volume",
    "news": "sentiment",
    "news_sentiment": "sentiment",
    "finbert": "sentiment",
    "social_sentiment": "social",
    "reddit": "social",
    "stocktwits": "social",
    "market_regime": "regime",
    "congress_trades": "congress",
    "insider": "insiders",
    "fundamental": "fundamentals",
    "ml": "ml_signal",
    "model": "ml_signal",
    "wiki": "narrative",
    "worldview": "narrative",
}

# Signal-shaped keys that are deliberately NOT predictive signals: they
# describe why an exit fired, not a read on the name before the trade.
# Recognized (no "unknown key" warning) but excluded from entry-row
# detection and from the scorecard tally.
NON_PREDICTIVE_SIGNAL_KEYS = frozenset({"stop_trigger", "exit_trigger"})

# training_examples.example_type values.
EXAMPLE_TYPE_ENTRY = "entry"
EXAMPLE_TYPE_EXIT = "exit"
EXAMPLE_TYPE_OBSERVATION = "observation"

_ACTION_TO_EXAMPLE_TYPE = {
    "BUY": EXAMPLE_TYPE_ENTRY,
    "SELL": EXAMPLE_TYPE_EXIT,
    "HOLD": EXAMPLE_TYPE_OBSERVATION,
}


def is_signal_shaped(value) -> bool:
    """A features entry carrying a scored signal ({direction, confidence}),
    as opposed to free-form context (a note string, a raw number)."""
    return isinstance(value, dict) and ("direction" in value or "confidence" in value)


def canonical_signal_key(name: str):
    """Canonical name for a signal key, or None if unrecognized."""
    if name in CANONICAL_SIGNAL_KEYS or name in NON_PREDICTIVE_SIGNAL_KEYS:
        return name
    return SIGNAL_KEY_ALIASES.get(name)


def canonicalize_features(features):
    """Fold alias signal keys into their canonical name. Returns
    (features, warnings) and never raises -- features stays free-form on
    purpose, this only tidies the keys the scorecard counts.

    Only signal-shaped entries are touched; free-form context keys pass
    through untouched (an `entry` metadata blob, a `note` string, a raw
    `rsi: 50.9` number are all left exactly as-is).

    When several keys collapse onto one canonical name in the same blob
    (`macd_hist` + `rsi` + `technical` are all one 'technical' read, which
    is exactly how live rows were written), they are combined with
    signals.reconcile_signals -- the module already responsible for turning
    several (direction, confidence) reads into one -- and the originals are
    preserved verbatim under the canonical entry's `components` so nothing
    is discarded."""
    if not isinstance(features, dict):
        return features, []

    result = {}
    warnings = []
    groups = {}       # canonical name -> {original key: entry}
    group_order = []

    for name, val in features.items():
        if not is_signal_shaped(val):
            result[name] = val
            continue
        canonical = canonical_signal_key(name)
        if canonical is None:
            warnings.append(
                f"unrecognized signal key {name!r} -- not in decisions.CANONICAL_SIGNAL_KEYS; "
                f"kept as-is, but it will tally separately in signal_scorecard.py. "
                f"Add it to CANONICAL_SIGNAL_KEYS/SIGNAL_KEY_ALIASES if it's real."
            )
            result[name] = val
            continue
        if canonical not in groups:
            groups[canonical] = {}
            group_order.append(canonical)
        groups[canonical][name] = val

    for canonical in group_order:
        members = groups[canonical]
        if len(members) == 1:
            (name, val), = members.items()
            if name != canonical:
                warnings.append(f"signal key {name!r} normalized to {canonical!r}")
            result[canonical] = val
            continue
        combined = _combine_signal_group(canonical, members)
        warnings.append(
            f"signal keys {sorted(members)} all mean {canonical!r} -- combined into one "
            f"{combined['direction']}/{combined['confidence']} read (originals kept under "
            f"{canonical}.components)"
        )
        result[canonical] = combined
    return result, warnings


def _combine_signal_group(canonical, members):
    """One canonical entry from several reads of the same signal. Direction
    comes from signals.reconcile_signals' recommendation ('conflicted' ->
    'neutral', since the scorecard only understands the three directions),
    confidence from its combined_confidence."""
    reconciled = signals.reconcile_signals({
        name: val for name, val in members.items()
        if isinstance(val, dict) and "direction" in val and "confidence" in val
    })
    direction = reconciled["recommendation"]
    if direction not in signals.DIRECTIONS:
        direction = "neutral"
    return {
        "direction": direction,
        "confidence": reconciled["combined_confidence"],
        "components": dict(members),
    }


def record_decision(trader_id, ticker, action, rationale="", conviction=0.0,
                     regime=None, features=None, db_path: Path = None,
                     position_entry_time=None, source=None):
    """Write a decisions row + seed (or enrich) the matching training_examples row.

    features should ideally use signals.py's per-signal shape
    ({"sentiment": {"direction": ..., "confidence": ...}, "technical": {...}, ...})
    so training_examples rows are actually queryable per-signal later. This is
    a soft check, not enforced — free-form features still work, they just
    won't be usable for signal-level attribution. Signal key spellings ARE
    normalized (see canonicalize_features).

    On a BUY, executor.py has normally already written the entry row
    mechanically at fill time (2026-08-01). This call then MERGES its
    richer, agent-scored features into that same row rather than inserting a
    second one — one position, one entry row, no ambiguity about which row a
    later close labels. Only if no such row exists (executor's write failed,
    or this is a dry-run/backfill) does it insert a fresh one."""
    features = features or {}
    features, key_warnings = canonicalize_features(features)
    if key_warnings:
        log.warning("record_decision(%s/%s): feature key warnings: %s", trader_id, ticker, key_warnings)
    warnings = signals.validate_signal_features(features)
    if warnings:
        log.warning("record_decision(%s/%s): features shape warnings: %s", trader_id, ticker, warnings)
    ts = datetime.now(timezone.utc).isoformat()
    example_type = _ACTION_TO_EXAMPLE_TYPE.get(str(action).upper())

    try:
        conn = trader_db.get_conn(db_path)
    except Exception as e:
        log.error("record_decision(%s/%s): DB unavailable, not logged: %s", trader_id, ticker, e)
        return {"error": f"db unavailable: {e}", "decision_id": None, "training_example_id": None}

    try:
        decision_id = trader_db.insert_decision(
            conn, ticker=ticker, timestamp=ts, decision=action, conviction=conviction,
            rationale=rationale, regime=regime, decision_json=json.dumps({"features": features}),
            source=source,
        )
    except Exception as e:
        log.error("record_decision(%s/%s): DB write failed, not logged: %s", trader_id, ticker, e)
        conn.close()
        return {"error": f"db write failed: {e}", "decision_id": None, "training_example_id": None}

    merged_into_existing = False
    try:
        if example_type == EXAMPLE_TYPE_ENTRY:
            if position_entry_time is None:
                position_entry_time = _open_position_entry_time(conn, ticker)
            existing = _entry_row_for_position(conn, ticker, position_entry_time)
        else:
            existing = None

        if existing is not None:
            merged = _merge_features(existing.get("features"), features)
            trader_db.update_training_example_features(
                conn, training_example_id=existing["id"], features=json.dumps(merged),
                decision_id=decision_id, position_entry_time=position_entry_time,
            )
            training_example_id = existing["id"]
            merged_into_existing = True
        else:
            training_example_id = trader_db.insert_training_example(
                conn, ticker=ticker, features=json.dumps(features), decision_id=decision_id,
                created_at=ts, example_type=example_type, position_entry_time=position_entry_time,
            )
    except Exception as e:
        log.error("record_decision(%s/%s): training_example write failed: %s", trader_id, ticker, e)
        training_example_id = None
    finally:
        conn.close()
    return {"decision_id": decision_id, "training_example_id": training_example_id,
            "training_example_merged": merged_into_existing}


def _entry_row_for_position(conn, ticker, position_entry_time):
    """The open entry row to merge into, but only if it genuinely belongs to
    THIS position. find_entry_training_example's type fallback can return an
    older still-unlabeled row from a previous position in the same ticker;
    topping that one up would attach this trade's data to a different one."""
    row = trader_db.find_entry_training_example(conn, ticker, position_entry_time=position_entry_time)
    if row is None:
        return None
    if position_entry_time and row.get("position_entry_time") not in (None, position_entry_time):
        return None
    return row


def _open_position_entry_time(conn, ticker):
    """positions.entry_time for the open position in `ticker`, or None.
    Best-effort: a missing/closed position just means the training row goes
    unlinked, which the entry-type fallback still handles."""
    try:
        row = trader_db.get_position(conn, str(ticker).upper())
    except Exception:
        return None
    if not row or row.get("status") != "open":
        return None
    return row.get("entry_time")


def _merge_features(existing_json, new_features):
    """New (agent-scored) features win on key collision; anything the
    existing row had that the new blob doesn't mention is preserved —
    executor.py's mechanical entry row carries fill metadata the agent's
    own call has no way to know."""
    try:
        existing = json.loads(existing_json) if existing_json else {}
    except (TypeError, ValueError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    merged = dict(existing)
    merged.update(new_features or {})
    return merged


def record_entry_example(ticker, features=None, position_entry_time=None, decision_id=None,
                          created_at=None, db_path: Path = None):
    """Write (or top up) the ENTRY training_examples row for a position —
    called mechanically by executor.py the moment a BUY fills, 2026-08-01.

    Why this exists: the training row used to be written only when the agent
    remembered to run `record_decision.py decision` as a separate manual
    step, which it skipped whenever a tick ran short on time — only ~45% of
    executed trades ever got a row. The self-improvement loop can't learn
    from trades it has no record of, so the row is now written in code on
    every fill, with whatever signal data is available at that moment.
    `record_decision.py decision` still exists for the qualitative rationale
    and richer scored signals; it merges into this same row.

    Idempotent per position: a scale-in on an already-open position merges
    into the existing row (positions.entry_time doesn't move on a scale-in)
    rather than creating a second entry row for one position."""
    features = features or {}
    features, key_warnings = canonicalize_features(features)
    if key_warnings:
        log.warning("record_entry_example(%s): feature key warnings: %s", ticker, key_warnings)
    created_at = created_at or datetime.now(timezone.utc).isoformat()

    try:
        conn = trader_db.get_conn(db_path)
    except Exception as e:
        log.error("record_entry_example(%s): DB unavailable, not logged: %s", ticker, e)
        return {"error": f"db unavailable: {e}", "training_example_id": None}

    try:
        existing = _entry_row_for_position(conn, ticker, position_entry_time)
        if existing is not None:
            merged = _merge_features(existing.get("features"), features)
            trader_db.update_training_example_features(
                conn, training_example_id=existing["id"], features=json.dumps(merged),
                decision_id=decision_id, position_entry_time=position_entry_time,
            )
            return {"training_example_id": existing["id"], "training_example_merged": True}

        te_id = trader_db.insert_training_example(
            conn, ticker=ticker, features=json.dumps(features), decision_id=decision_id,
            created_at=created_at, example_type=EXAMPLE_TYPE_ENTRY,
            position_entry_time=position_entry_time,
        )
        return {"training_example_id": te_id, "training_example_merged": False}
    except Exception as e:
        log.error("record_entry_example(%s): write failed: %s", ticker, e)
        return {"error": f"db write failed: {e}", "training_example_id": None}
    finally:
        conn.close()


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


def find_labelable_entry_row(conn, ticker, position_entry_time=None):
    """The training_examples row a close should label, or None.

    Tier 1/2 live in trader_db.find_entry_training_example (exact
    position_entry_time link, then newest unlabeled example_type='entry'
    row).

    Tier 3 is the pre-2026-08-01 legacy case: rows written before
    example_type existed, where BUY/SELL rows are indistinguishable by
    column. Those are only usable if the row actually carries scored,
    recognized predictive signals -- a SELL's {"stop_trigger": {...}} blob
    or a {"reason": "..."} note is exactly the wrong row to label (that
    mislabeling is the bug this function exists to fix), so it's skipped."""
    row = trader_db.find_entry_training_example(conn, ticker, position_entry_time=position_entry_time)
    if row is not None:
        return row
    for legacy in trader_db.unlabeled_legacy_training_examples(conn, ticker):
        if _carries_predictive_signals(legacy.get("features")):
            return legacy
    return None


def _carries_predictive_signals(features_json) -> bool:
    try:
        features = json.loads(features_json) if features_json else {}
    except (TypeError, ValueError):
        return False
    if not isinstance(features, dict):
        return False
    for name, val in features.items():
        if not is_signal_shaped(val):
            continue
        canonical = canonical_signal_key(name)
        if canonical and canonical not in NON_PREDICTIVE_SIGNAL_KEYS:
            return True
    return False


def record_trade_close(trader_id, ticker, trade_id, pnl, return_pct, db_path: Path = None,
                        position_entry_time=None):
    """Label the ENTRY training_examples row for `ticker` now that the
    position has actually closed. trade_id is opaque/optional -- Stonks has
    no trades table of its own (see record_decision.py's CLI), passed
    through only for the training_examples.trade_id column.

    position_entry_time (positions.entry_time of the position being closed,
    passed by executor.py) is the precise correlation key; without it this
    falls back to the newest open entry row for the ticker. It deliberately
    never labels "the newest unlabeled row of any type" -- on a SELL that is
    the SELL's own row, whose features describe the exit trigger rather than
    the entry signals the outcome is meant to validate."""
    try:
        conn = trader_db.get_conn(db_path)
    except Exception as e:
        log.error("record_trade_close(%s/%s): DB unavailable, not labeled: %s", trader_id, ticker, e)
        return {"error": f"db unavailable: {e}", "training_example_id": None, "labeled": False}

    try:
        row = find_labelable_entry_row(conn, ticker, position_entry_time=position_entry_time)
        if row is None:
            conn.close()
            return {"error": f"no unlabeled entry training_examples row found for {trader_id}/{ticker}",
                     "training_example_id": None, "labeled": False}
        training_example_id = row["id"]
    except Exception as e:
        log.error("record_trade_close(%s/%s): DB query failed, not labeled: %s", trader_id, ticker, e)
        conn.close()
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
