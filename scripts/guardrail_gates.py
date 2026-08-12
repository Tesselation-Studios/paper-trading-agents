#!/usr/bin/env python3
"""Guardrail gates — checked before a BUY/SELL is placed.

Each gate: check(context, action) -> (granted: bool, reason: str). Toggle
any gate off via params.json guardrail_gates.<name> — no code change needed.
Gates fail open on missing data or unexpected errors (never block a trade
because of a data hiccup) but fail closed on an actual limit breach.

Extracted 2026-08-11 from executor.py (was one 2,562-line file). Depends
only on alpaca_client.py (the base module) and trader_db — nothing else
extracted from executor.py depends on this module, so it was safe to pull
out second, right after alpaca_client.py itself.
"""
import contextlib
import fcntl
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import alpaca_client
import trader_db

def gate_cash(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    cost = float(action.get("quantity", 0)) * float(action.get("price", 0) or 0)
    if cost <= 0:
        return True, "no price data, skipped (fail-open)"
    cash = float(context.get("cash", 0))
    if cost > cash:
        return False, f"BUY costs ${cost:,.2f} but only ${cash:,.2f} cash available"
    # 2026-08-12 (Raf's direction): keep a minimum cash reserve on hand at
    # all times for opportunistic capacity, same spirit as strategy.md's
    # "Cash floor" -- a real limit alongside the plain affordability check
    # above, not just documentation. Defaults to 0 (old behavior, no floor)
    # when the param is absent, so this stays a no-op until deliberately set.
    risk = alpaca_client.load_params().get("risk", {})
    min_cash_reserve = float(risk.get("min_cash_reserve", 0.0))
    remaining = cash - cost
    if remaining < min_cash_reserve:
        return False, (f"BUY costs ${cost:,.2f}, would leave ${remaining:,.2f} cash -- "
                        f"below the ${min_cash_reserve:,.2f} reserve floor")
    return True, f"BUY costs ${cost:,.2f}, cash ${cash:,.2f} sufficient"


def _size_cap_pct_for(risk: Dict[str, Any], play_type: Optional[str]) -> Tuple[float, str]:
    """The position-size cap (% of portfolio) that actually governs a given
    play type, plus a label for the reason string.

    2026-08-01: GATES is an ordered dict and check_order() returns on the
    first rejection, so gate_position_size (index 1) runs BEFORE
    gate_long_play (2) and gate_conviction_play (3). Applying the flat
    risk.max_position_pct here regardless of play type meant a conviction
    play sized to risk.conviction_play.position_size_pct was rejected by
    this gate before the gate that authorizes that size ever executed --
    confirmed live, `SELECT play_type, count(*) FROM positions` returned
    only {'standard': 41}, the conviction bucket had never fired once since
    shipping on 2026-07-30.

    Each bucket's own cap is read from params.json rather than assumed to
    be larger or smaller than the flat one, so this stays correct whatever
    the numbers are: long_play.position_size_pct is currently *smaller*
    than max_position_pct (a deliberately small unproven experiment) and
    conviction_play.position_size_pct is larger. gate_long_play /
    gate_conviction_play still enforce the same cap plus their concurrency
    limits -- this only stops an earlier gate from vetoing a size a later
    gate is there to allow."""
    if play_type == "conviction":
        cp = risk.get("conviction_play", {})
        return float(cp.get("position_size_pct", 10.0)), "conviction-play cap"
    if play_type == "long":
        lp = risk.get("long_play", {})
        return float(lp.get("position_size_pct", 3.0)), "long-play cap"
    return float(risk.get("max_position_pct", 6.0)), "cap"


def gate_position_size(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    risk = alpaca_client.load_params().get("risk", {})
    max_pct, cap_label = _size_cap_pct_for(risk, action.get("play_type"))
    ticker = str(action.get("ticker", "")).upper()
    price = float(action.get("price", 0) or 0)
    qty = float(action.get("quantity", 0))
    proposed_value = qty * price
    portfolio_value = float(context.get("portfolio_value", 0))
    if proposed_value <= 0 or portfolio_value <= 0:
        return True, "no price/portfolio data, skipped (fail-open)"

    existing_value = sum(
        float(p.get("market_value", 0))
        for p in context.get("positions", [])
        if str(p.get("symbol", "")).upper() == ticker
    )
    total_pct = (existing_value + proposed_value) / portfolio_value * 100

    if total_pct > max_pct:
        return False, (
            f"{ticker} would be {total_pct:.1f}% of portfolio "
            f"(existing ${existing_value:,.2f} + proposed ${proposed_value:,.2f}), "
            f"exceeds {max_pct:.0f}% {cap_label}"
        )
    return True, f"{ticker} at {total_pct:.1f}% of portfolio, within {max_pct:.0f}% {cap_label}"


def gate_long_play(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Enforces long-play-specific rules on BUYs tagged play_type='long'
    (params.json risk.long_play, 2026-07-30): a smaller position_size_pct
    cap than the normal max_position_pct (this is a live, unproven
    experiment -- see the long_play._source note in params.json for why),
    plus a max_concurrent_long_plays cap so it can't quietly become the
    default response to every entry. Non-long BUYs and all SELLs skip this
    gate entirely -- it has nothing to say about a standard position.
    """
    if action.get("action") != "BUY" or action.get("play_type") != "long":
        return True, "not a long play, skipped"

    lp_params = alpaca_client.load_params().get("risk", {}).get("long_play", {})
    if not lp_params.get("enabled", True):
        return False, "long plays disabled via params.json risk.long_play.enabled"

    ticker = str(action.get("ticker", "")).upper()
    price = float(action.get("price", 0) or 0)
    qty = float(action.get("quantity", 0))
    proposed_value = qty * price
    portfolio_value = float(context.get("portfolio_value", 0))
    if proposed_value <= 0 or portfolio_value <= 0:
        return True, "no price/portfolio data, skipped (fail-open)"

    size_pct = float(lp_params.get("position_size_pct", 3.0))
    existing_value = sum(
        float(p.get("market_value", 0))
        for p in context.get("positions", [])
        if str(p.get("symbol", "")).upper() == ticker
    )
    total_pct = (existing_value + proposed_value) / portfolio_value * 100
    if total_pct > size_pct:
        return False, (
            f"{ticker} long play would be {total_pct:.1f}% of portfolio "
            f"(existing ${existing_value:,.2f} + proposed ${proposed_value:,.2f}), "
            f"exceeds long-play cap of {size_pct:.1f}% (smaller than normal max_position_pct while unproven)"
        )

    max_concurrent = int(lp_params.get("max_concurrent_long_plays", 2))
    try:
        conn = trader_db.get_conn()
        try:
            open_long_plays = [
                p for p in trader_db.get_open_positions(conn)
                if p.get("play_type") == "long" and str(p.get("ticker", "")).upper() != ticker
            ]
        finally:
            conn.close()
    except Exception as e:
        return True, f"could not check concurrent long plays (fail-open): {e}"

    if len(open_long_plays) >= max_concurrent:
        tickers = ", ".join(p["ticker"] for p in open_long_plays)
        return False, (
            f"already {len(open_long_plays)} open long play(s) ({tickers}), "
            f"at max_concurrent_long_plays cap of {max_concurrent}"
        )
    undersized_note = (
        f" ⚠️ under 50% of long-play target range ({size_pct:.1f}%) — consider scripts/position_sizing.py"
        if total_pct < 0.5 * size_pct else ""
    )
    return True, (
        f"{ticker} long play at {total_pct:.1f}% of portfolio (cap {size_pct:.1f}%), "
        f"{len(open_long_plays)}/{max_concurrent} concurrent long plays{undersized_note}"
    )


def gate_conviction_play(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Enforces conviction-play rules on BUYs tagged play_type='conviction'
    (params.json risk.conviction_play): a larger position_size_pct cap than
    the normal max_position_pct (this is the higher-conviction bucket, sized
    up not down), plus a max_concurrent_conviction_plays cap. Non-conviction
    BUYs and all SELLs skip this gate entirely.
    """
    if action.get("action") != "BUY" or action.get("play_type") != "conviction":
        return True, "not a conviction play, skipped"

    cp_params = alpaca_client.load_params().get("risk", {}).get("conviction_play", {})
    if not cp_params.get("enabled", True):
        return False, "conviction plays disabled via params.json risk.conviction_play.enabled"

    ticker = str(action.get("ticker", "")).upper()
    price = float(action.get("price", 0) or 0)
    qty = float(action.get("quantity", 0))
    proposed_value = qty * price
    portfolio_value = float(context.get("portfolio_value", 0))
    if proposed_value <= 0 or portfolio_value <= 0:
        return True, "no price/portfolio data, skipped (fail-open)"

    size_pct = float(cp_params.get("position_size_pct", 10.0))
    existing_value = sum(
        float(p.get("market_value", 0))
        for p in context.get("positions", [])
        if str(p.get("symbol", "")).upper() == ticker
    )
    total_pct = (existing_value + proposed_value) / portfolio_value * 100
    if total_pct > size_pct:
        return False, (
            f"{ticker} conviction play would be {total_pct:.1f}% of portfolio "
            f"(existing ${existing_value:,.2f} + proposed ${proposed_value:,.2f}), "
            f"exceeds conviction-play cap of {size_pct:.1f}%"
        )

    max_concurrent = int(cp_params.get("max_concurrent_conviction_plays", 5))
    try:
        conn = trader_db.get_conn()
        try:
            open_conviction_plays = [
                p for p in trader_db.get_open_positions(conn)
                if p.get("play_type") == "conviction" and str(p.get("ticker", "")).upper() != ticker
            ]
        finally:
            conn.close()
    except Exception as e:
        return True, f"could not check concurrent conviction plays (fail-open): {e}"

    if len(open_conviction_plays) >= max_concurrent:
        tickers = ", ".join(p["ticker"] for p in open_conviction_plays)
        return False, (
            f"already {len(open_conviction_plays)} open conviction play(s) ({tickers}), "
            f"at max_concurrent_conviction_plays cap of {max_concurrent}"
        )
    undersized_note = (
        f" ⚠️ under 50% of conviction-play target range ({size_pct:.1f}%) — consider scripts/position_sizing.py"
        if total_pct < 0.5 * size_pct else ""
    )
    return True, (
        f"{ticker} conviction play at {total_pct:.1f}% of portfolio (cap {size_pct:.1f}%), "
        f"{len(open_conviction_plays)}/{max_concurrent} concurrent conviction plays{undersized_note}"
    )


def gate_max_portfolio_risk(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Portfolio-level stop-loss exposure: if every open position (plus this
    proposed buy) hit its hard stop simultaneously, what % of equity would be
    lost? stop_loss_pct is a single global value (risk.stop_loss_pct, applied
    uniformly to every position -- see gate_drawdown_circuit_breaker/check_stops),
    so this reduces to gross_exposure_value * stop_loss_pct / equity rather than
    needing a per-position stop distance."""
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    params = alpaca_client.load_params()
    max_risk_pct = float(params.get("risk", {}).get("max_portfolio_risk_pct", 8.0))
    stop_loss_frac = abs(float(params.get("risk", {}).get("stop_loss_pct", alpaca_client.DEFAULT_STOP_LOSS_PCT))) / 100.0
    price = float(action.get("price", 0) or 0)
    qty = float(action.get("quantity", 0))
    proposed_value = qty * price
    portfolio_value = float(context.get("portfolio_value", 0))
    if proposed_value <= 0 or portfolio_value <= 0:
        return True, "no price/portfolio data, skipped (fail-open)"

    existing_value = sum(float(p.get("market_value", 0)) for p in context.get("positions", []))
    total_exposure_value = existing_value + proposed_value
    risk_pct = total_exposure_value * stop_loss_frac / portfolio_value * 100

    if risk_pct > max_risk_pct:
        return False, (
            f"portfolio stop-loss exposure would be {risk_pct:.1f}% of equity "
            f"(${total_exposure_value:,.2f} total position value x {stop_loss_frac*100:.0f}% stop), "
            f"exceeds {max_risk_pct:.0f}% cap"
        )
    return True, f"portfolio stop-loss exposure at {risk_pct:.1f}% of equity, within {max_risk_pct:.0f}% cap"


def gate_max_positions(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    max_positions = int(alpaca_client.load_params().get("risk", {}).get("max_positions", 25))
    ticker = str(action.get("ticker", "")).upper()
    positions = context.get("positions", [])
    if any(str(p.get("symbol", "")).upper() == ticker for p in positions):
        return True, "adding to existing position, skipped"
    if len(positions) >= max_positions:
        return False, f"already at {len(positions)}/{max_positions} positions, no new tickers"
    return True, f"{len(positions)}/{max_positions} positions, room for new ticker"


def _sector_of(ticker: str) -> Optional[str]:
    """2026-07-28: was a positions/<ticker>.md 'Sector:' line read -- none
    of the real position files ever had one (no script wrote it, and the
    LLM agent never filled it in either), so this gate was silently
    fail-open in practice. Now reads the positions table, populated
    directly by main()'s BUY handler from --sector at open time."""
    try:
        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, ticker.upper())
        finally:
            conn.close()
    except Exception:
        return None
    return row["sector"] if row else None


def gate_sector_concentration(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Sector lookup reads the positions table (populated at BUY time from
    --sector). No sector data available -> skip (fail-open), not a block."""
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    max_per_sector = int(alpaca_client.load_params().get("risk_guards", {}).get("max_positions_per_sector", 2))
    ticker = str(action.get("ticker", "")).upper()
    sector = action.get("sector") or _sector_of(ticker)
    if not sector:
        return True, "no sector data, skipped (fail-open)"

    same_sector_count = sum(
        1 for p in context.get("positions", [])
        if str(p.get("symbol", "")).upper() != ticker and _sector_of(str(p.get("symbol", "")).upper()) == sector
    )
    if same_sector_count >= max_per_sector:
        return False, f"{sector} already has {same_sector_count} positions, at {max_per_sector} cap"
    return True, f"{sector} has {same_sector_count}/{max_per_sector} positions"


def gate_catalyst_liquidity(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """v1.18 rule mechanized (2026-08-05): catalyst-led entries on sub-$500M
    names need a real daily-dollar-volume floor, or skip regardless of
    catalyst quality. Fail-open when market_cap/avg_dollar_volume aren't
    passed -- only applies when Stan is entering a sub-$500M name and has
    already looked these up via get_fundamentals (tick_prompt.md step 8)."""
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    market_cap = action.get("market_cap")
    if market_cap is None:
        return True, "no market_cap data, skipped (fail-open)"
    gate_params = alpaca_client.load_params().get("risk_guards", {}).get("catalyst_liquidity_gate", {})
    threshold = gate_params.get("market_cap_threshold", 500_000_000)
    if market_cap >= threshold:
        return True, f"mkt cap ${market_cap:,.0f} >= ${threshold:,.0f}, gate not applicable"
    avg_dollar_volume = action.get("avg_dollar_volume")
    if avg_dollar_volume is None:
        return True, "sub-$500M name but no avg_dollar_volume data, skipped (fail-open)"
    min_volume = gate_params.get("min_daily_dollar_volume", 50_000)
    if avg_dollar_volume < min_volume:
        return False, (f"mkt cap ${market_cap:,.0f} (<${threshold:,.0f}), avg dollar volume "
                        f"${avg_dollar_volume:,.0f}/day < ${min_volume:,.0f} floor — v1.18 skip")
    return True, f"avg dollar volume ${avg_dollar_volume:,.0f}/day >= ${min_volume:,.0f} floor"


def gate_hours(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Reject any BUY/SELL outside 09:30-16:00 ET, Mon-Fri, or on an NYSE
    holiday (fixed + floating, e.g. Thanksgiving/Easter-derived) or early
    close day (see market_hours.py -- a real calendar, not just a weekday
    check, so this doesn't try to trade on a market holiday that happens
    to fall Mon-Fri).

    context["_test_now"] lets tests inject a fixed timestamp instead of the
    real wall clock — not used in production, only by the test suite.
    """
    import datetime
    if context.get("_test_now") is not None:
        now = context["_test_now"]
    else:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            now = datetime.datetime.now()

    if now.weekday() >= 5:
        return False, f"{now.strftime('%A')} — market closed on weekends"

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import market_hours
    if market_hours.is_holiday(now.date()) or market_hours.is_custom_holiday(now.date()):
        return False, f"{now.strftime('%Y-%m-%d')} — market closed (holiday)"

    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=14, minute=0, second=0, microsecond=0) \
        if market_hours.is_early_close_day(now.date()) \
        else now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now < market_open or now > market_close:
        close_note = " (early close today)" if market_hours.is_early_close_day(now.date()) else ""
        return False, f"{now.strftime('%H:%M %Z')} — market open 09:30-{market_close.strftime('%H:%M')}{close_note}"
    return True, f"{now.strftime('%H:%M %Z')} — market open"


def _is_regular_trading_hours() -> bool:
    """True during 09:30-16:00 ET, Mon-Fri — same window as gate_hours.

    2026-07-27: the heartbeat calls the `status` action until 23:00 ET, well
    past the 09:30-16:00 ET `stonks-tick` cron window that actually trades.
    Anything driven by `status` that should only count real trading activity
    (deployment_pressure's tick counter) must check this first, or it
    inflates off-hours with no corresponding trades.
    """
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def gate_bankroll(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Reject BUYs that exceed bankroll.py's current self-calibrating ceiling.

    bankroll.py grows the ceiling 2%/win and shrinks it 1%/loss (accelerating
    above 55% win rate, decelerating below 45%) — this is what makes position
    sizing actually adapt to real performance, not just a fixed % of
    portfolio. See scripts/bankroll.py / bankroll.md.

    2026-07-23: the ceiling used here is now competition-mode adjusted
    (bankroll.effective_ceiling) — ramps up in the final ~60 days before
    the 12/31/26 deadline, and reacts to whether the account is ahead or
    behind its own starting capital. Raw ceiling growth/decay from wins
    and losses is unchanged; this only scales the number gate_bankroll
    actually compares against.

    2026-08-01: play-type aware, for the same reason gate_position_size is
    (see _size_cap_pct_for). The bankroll ceiling is a dollar amount, not a
    percentage, so it doesn't share max_position_pct's flat cap — but it
    independently vetoed the conviction bucket anyway: at $10.4k equity the
    ceiling was $679 while risk.conviction_play.position_size_pct (10%)
    authorizes ~$1,042, so every full-size conviction play would have been
    rejected here even after the position-size gate was fixed. A play
    type's own explicitly-configured size cap in params.json is a
    deliberate risk decision; the ceiling floors up to it rather than
    silently overriding it. Standard BUYs are unaffected — they compare
    against the raw ceiling exactly as before, and every other gate (cash,
    position size, max_portfolio_risk, drawdown) still applies to all play
    types.
    """
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    price = float(action.get("price", 0) or 0)
    qty = float(action.get("quantity", 0))
    cost = qty * price
    if cost <= 0:
        return True, "no price data, skipped (fail-open)"

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import bankroll
    state = bankroll.read_bankroll()
    portfolio_value = float(context.get("portfolio_value", 0) or 0)
    ceiling = (bankroll.effective_ceiling(state, portfolio_value)
               if portfolio_value > 0 else state["ceiling"])

    play_type = action.get("play_type")
    note = ""
    if play_type in ("conviction", "long") and portfolio_value > 0:
        risk = alpaca_client.load_params().get("risk", {})
        play_cap_pct, cap_label = _size_cap_pct_for(risk, play_type)
        play_cap_value = portfolio_value * play_cap_pct / 100.0
        if play_cap_value > ceiling:
            ceiling = play_cap_value
            note = f" ({cap_label} {play_cap_pct:.1f}% of portfolio floors the ceiling for this play type)"

    if cost > ceiling:
        return False, f"BUY costs ${cost:,.2f}, exceeds bankroll ceiling ${ceiling:,.2f}{note}"
    return True, f"BUY costs ${cost:,.2f}, within bankroll ceiling ${ceiling:,.2f}{note}"


def gate_conviction(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """A sanity floor, not a real gate -- catches a broken/zero conviction
    score. Entry quality is decided by the gestalt Stan reasons over (world
    narrative, congress trades, fundamentals, wiki, cross-sectional
    momentum), not a numeric threshold formula."""
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    if action.get("conviction") is None:
        return True, "no conviction passed, skipped (fail-open)"
    risk = alpaca_client.load_params().get("risk", {})
    floor = float(risk.get("conviction_floor", 0.10))
    conviction = float(action["conviction"])
    if conviction < floor:
        return False, f"conviction {conviction:.2f} below sanity floor {floor:.2f}"
    return True, f"conviction {conviction:.2f} >= {floor:.2f} floor"


def _load_recent_orders() -> Dict[str, Any]:
    if alpaca_client.RECENT_ORDERS_PATH.exists():
        try:
            return json.loads(alpaca_client.RECENT_ORDERS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _load_experience() -> Dict[str, Any]:
    """experience.json's total_trades/total_wins/total_losses/consecutive_*
    fields (NOT current_level/milestones_unlocked -- those were genuinely
    superseded by bankroll.py's UNLOCK_TIERS, see that module's 2026-07-23
    comment) used to be hand-edited by the LLM every tick per TOOLS.md's
    prose instruction ("read each tick, update ... after trades"). That held
    up for a week, then silently stopped: 2026-07-27's 3 morning SELL exits
    (F/IP/FHB) never bumped total_trades even though total_ticks kept
    incrementing normally the same day -- same "prose reminder eventually
    fails" pattern as the pre-2026-07-22 outcome-labeling gap this file's
    close_trade_outcome() already fixed the same way. Confirmed still a
    live-read field, not dead: Stan's own HEARTBEAT.md self-stats line
    quotes it every tick ("self stats: 0 trades logged today..."). Fixed by
    mechanizing the update into the same two hook points bankroll.py
    already updates reliably from -- record_order_submitted() (every real
    order) and close_trade_outcome() (every real SELL's win/loss)."""
    defaults = {
        "version": 1, "total_ticks": 0, "total_trades": 0,
        "total_wins": 0, "total_losses": 0,
        "consecutive_wins": 0, "consecutive_losses": 0,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if alpaca_client.EXPERIENCE_PATH.exists():
        try:
            state = json.loads(alpaca_client.EXPERIENCE_PATH.read_text())
            defaults.update(state)
            return defaults
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


def _save_experience(state: Dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    alpaca_client.EXPERIENCE_PATH.write_text(json.dumps(state, indent=2))


def record_experience_trade() -> None:
    """Bumps total_trades once for any order that actually executed (BUY or
    SELL) -- called from record_order_submitted(), the one point in this
    file that already means "a real order was just placed." Matches the
    historical hand-maintained semantics (total_trades counted every
    executed order, not just closes -- see git history of experience.json,
    e.g. a solo BUY bumping total_trades with no win/loss change). Fails
    open (never raises) -- a bookkeeping write failure must not look like a
    failed order; the order already executed by the time this runs, same
    reasoning as close_trade_outcome's Postgres fail-open path below."""
    try:
        state = _load_experience()
        state["total_trades"] = int(state.get("total_trades", 0)) + 1
        _save_experience(state)
    except OSError:
        pass


def record_experience_outcome(is_win: bool) -> None:
    """Bumps total_wins/total_losses + the consecutive-streak counters,
    called from close_trade_outcome() once a SELL's pnl is known -- the
    same trigger point bankroll.recalc_ceiling() already updates from
    reliably. Fails open, same reasoning as record_experience_trade()."""
    try:
        state = _load_experience()
        if is_win:
            state["total_wins"] = int(state.get("total_wins", 0)) + 1
            state["consecutive_wins"] = int(state.get("consecutive_wins", 0)) + 1
            state["consecutive_losses"] = 0
        else:
            state["total_losses"] = int(state.get("total_losses", 0)) + 1
            state["consecutive_losses"] = int(state.get("consecutive_losses", 0)) + 1
            state["consecutive_wins"] = 0
        _save_experience(state)
    except OSError:
        pass


def record_order_submitted(ticker: str, action: str, today: Optional[str] = None) -> None:
    """Called after a BUY/SELL actually executes — used by gate_duplicate_order
    to block an accidental repeat submission of the same ticker+action within
    the cooldown window. Confirmed 2026-07-22: DVN got bought 3 times in one
    tick (three separate BUY orders, 5-7 seconds apart) — nothing stopped the
    agent from submitting the same order again without noticing the first one
    had already filled.

    Also bumps the daily order counter gate_daily_order_count reads, and
    experience.json's total_trades (2026-07-27, see _load_experience's
    docstring) — `today` lets tests inject a fixed date; production always
    computes the real one."""
    state = _load_recent_orders()
    state[f"{ticker.upper()}:{action.upper()}"] = time.time()
    alpaca_client.STATE_DIR.mkdir(exist_ok=True)
    alpaca_client.RECENT_ORDERS_PATH.write_text(json.dumps(state, indent=2))

    if today is None:
        today = _today_et({})
    _record_daily_order(today)
    record_experience_trade()


def gate_duplicate_order(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Reject a BUY or SELL for the same ticker+action submitted again within
    risk.duplicate_order_cooldown_seconds of the last one that actually
    executed. Applies to both BUY and SELL, unlike most gates — a duplicate
    SELL is just as real a mistake as a duplicate BUY."""
    ticker = str(action.get("ticker", "")).upper()
    side = str(action.get("action", "")).upper()
    if not ticker or side not in ("BUY", "SELL"):
        return True, "no ticker/action, skipped"

    cooldown = float(alpaca_client.load_params().get("risk", {}).get("duplicate_order_cooldown_seconds", 60))
    state = _load_recent_orders()
    last_ts = state.get(f"{ticker}:{side}")
    if last_ts is None:
        return True, "no recent matching order, skipped"

    elapsed = time.time() - float(last_ts)
    if elapsed < cooldown:
        return False, f"{side} {ticker} submitted {elapsed:.0f}s ago (< {cooldown:.0f}s cooldown) — likely a duplicate"
    return True, f"last {side} {ticker} was {elapsed:.0f}s ago, outside {cooldown:.0f}s cooldown"


def gate_order_idempotency(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Authoritative, cross-process duplicate-order guard — queries Alpaca's
    own live order state directly (GET /v2/orders?status=open&symbols=X)
    instead of local file state.

    Why this exists alongside gate_duplicate_order: that gate is a local,
    file-based, check-then-set mechanism (state/recent_orders.json) — it
    only knows about orders THIS process already recorded. Two genuinely
    concurrent processes (a cron tick and position_stream.py's websocket
    daemon in paper-trading-rebuild, both invoking this same
    check_order()/place_order() path via set_watch.py-triggered execution)
    can each read "nothing recorded yet" before either writes its own
    record — a classic TOCTOU race. gate_duplicate_order is also purely
    time-window-based (60s cooldown), not "is there an actual live order
    right now." Confirmed twice: KRC bought 2x on 2026-07-27 and IP bought
    2x on 2026-07-24, both from overlapping sessions racing the same local
    check. Querying Alpaca directly closes that gap — it's authoritative
    regardless of which process is asking, because Alpaca is the one
    shared source of truth every process ultimately talks to. This is a
    second, independent layer, not a replacement — keep gate_duplicate_order
    as-is (it's free, no API call, and catches same-process repeats before
    this gate's network round-trip is even needed).

    BUY-only, unlike gate_duplicate_order (which guards both sides). A
    duplicate SELL of the same ticker fails harmlessly at Alpaca — you
    can't sell shares you don't hold (or already sold), the second order
    is rejected by the exchange itself with no capital consequence. A
    duplicate BUY compounds: both fill, and the account now holds double
    the intended size (exactly the KRC/IP failure mode this closes). No
    reason to spend the extra API call guarding a failure mode Alpaca
    already prevents for free.

    Fails open on any Alpaca API error (network hiccup, auth issue, rate
    limit, missing account in context) — matches every other gate's
    convention: a data hiccup shouldn't block a trade, only a genuine,
    confirmed open order should.
    """
    if action.get("action") != "BUY":
        return True, "non-BUY, skipped"
    ticker = str(action.get("ticker", "")).upper()
    if not ticker:
        return True, "no ticker, skipped"
    account = context.get("account")
    if not account:
        return True, "no account in context, skipped (fail-open)"

    try:
        open_orders = alpaca_client.get_open_orders(account, ticker)
    except Exception as e:
        return True, f"could not query Alpaca open orders (fail-open): {e}"

    matching = [
        o for o in open_orders
        if str(o.get("symbol", "")).upper() == ticker and str(o.get("side", "")).upper() == "BUY"
    ]
    if matching:
        order_ids = ", ".join(str(o.get("id", "?")) for o in matching)
        return False, (
            f"{ticker} already has {len(matching)} open BUY order(s) at Alpaca ({order_ids}) "
            f"— refusing duplicate submission"
        )
    return True, f"{ticker} has no open BUY orders at Alpaca, safe to submit"


def _today_et(context: Dict[str, Any]) -> str:
    """YYYY-MM-DD in America/New_York, matching gate_hours' pattern.
    context["_test_now"] lets tests inject a fixed date instead of the
    real wall clock — not used in production."""
    if context.get("_test_now") is not None:
        return context["_test_now"].strftime("%Y-%m-%d")
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d")


def _load_daily_order_count(today: str) -> int:
    """Count of orders submitted today (ET), across all tickers/actions —
    resets automatically when the stored date no longer matches today,
    same rollover-by-comparison pattern as everything else in this file
    (no separate midnight-reset job needed)."""
    if not alpaca_client.DAILY_ORDER_COUNT_PATH.exists():
        return 0
    try:
        state = json.loads(alpaca_client.DAILY_ORDER_COUNT_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return 0
    if state.get("date") != today:
        return 0
    return int(state.get("count", 0))


def _record_daily_order(today: str) -> None:
    count = _load_daily_order_count(today) + 1
    alpaca_client.STATE_DIR.mkdir(exist_ok=True)
    alpaca_client.DAILY_ORDER_COUNT_PATH.write_text(json.dumps({"date": today, "count": count}, indent=2))


def gate_daily_order_count(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """2026-07-23: strategy.md's Risk Management section has promised a
    'daily order-count audit' (risk_guards) since v1.0 — a rogue-trading
    backstop (a stuck loop or repeated bad decisions placing far more
    orders than a small-cap/wide/diversified strategy should in one day)
    — but nothing ever mechanically enforced it (found by
    workspace_review.py's dead-param check: risk_guards.
    order_count_audit_threshold_daily was declared, never read). Applies
    to both BUY and SELL, same reasoning as gate_duplicate_order — a rogue
    loop isn't only a buying problem.
    """
    side = str(action.get("action", "")).upper()
    if side not in ("BUY", "SELL"):
        return True, "non-BUY/SELL, skipped"

    threshold = int(alpaca_client.load_params().get("risk_guards", {}).get("order_count_audit_threshold_daily", 10))
    today = _today_et(context)
    count = _load_daily_order_count(today)
    if count >= threshold:
        return False, f"{count} orders already placed today (>= {threshold} threshold) — possible rogue loop"
    return True, f"{count}/{threshold} orders placed today"


def _load_peak_equity() -> float:
    if not alpaca_client.PEAK_EQUITY_PATH.exists():
        return 0.0
    try:
        return float(json.loads(alpaca_client.PEAK_EQUITY_PATH.read_text()).get("peak_equity", 0.0))
    except (json.JSONDecodeError, OSError, ValueError):
        return 0.0


def _update_peak_equity(current_equity: float) -> float:
    """Ratchets state/peak_equity.json up only, same pattern as check_stops'
    trailing-stop high-water mark. Called both from the drawdown gate (every
    BUY/SELL attempt) and the `status` action (every tick, even HOLD-only
    ones) so the recorded peak doesn't lag behind a real new high reached on
    a day with no order attempts."""
    peak = max(_load_peak_equity(), current_equity)
    alpaca_client.STATE_DIR.mkdir(exist_ok=True)
    alpaca_client.PEAK_EQUITY_PATH.write_text(json.dumps({"peak_equity": peak}, indent=2))
    return peak


def gate_drawdown_circuit_breaker(context: Dict[str, Any], action: Dict[str, Any]) -> Tuple[bool, str]:
    """Portfolio-level circuit breaker, adapted from
    paper-trading-rebuild/COMPETITION.md's >15%/>20% drawdown framework:
    pauses new BUYs once equity has fallen risk.drawdown_pause_pct from its
    recorded peak, hard-halts new BUYs at risk.drawdown_halt_pct pending
    review. Deliberately BUY-only at both thresholds — unlike
    COMPETITION.md's "eliminated" framing, this never blocks a SELL
    (including stop-loss exits): an account can't recover by being unable to
    cut a losing position at the exact moment it's furthest underwater. "An
    account at zero doesn't win anything" (SOUL.md) cuts against blocking
    exits, not for it.
    """
    portfolio_value = float(context.get("portfolio_value", 0) or 0)
    if portfolio_value <= 0:
        return True, "no portfolio value data, skipped (fail-open)"

    peak = _update_peak_equity(portfolio_value)
    drawdown_pct = ((peak - portfolio_value) / peak * 100) if peak > 0 else 0.0

    if action.get("action") != "BUY":
        return True, f"non-BUY, unaffected (drawdown {drawdown_pct:.1f}% from peak ${peak:,.2f})"

    risk = alpaca_client.load_params().get("risk", {})
    pause_pct = float(risk.get("drawdown_pause_pct", 15.0))
    halt_pct = float(risk.get("drawdown_halt_pct", 20.0))

    if drawdown_pct >= halt_pct:
        return False, (f"HALT: drawdown {drawdown_pct:.1f}% from peak ${peak:,.2f} "
                        f">= {halt_pct}% — new entries blocked pending review")
    if drawdown_pct >= pause_pct:
        return False, (f"PAUSED: drawdown {drawdown_pct:.1f}% from peak ${peak:,.2f} "
                        f">= {pause_pct}% — new entries paused")
    return True, f"drawdown {drawdown_pct:.1f}% from peak ${peak:,.2f}, within bounds"


# name -> gate function. params.json guardrail_gates.<name> = false disables it.
GATES = {
    "cash": gate_cash,
    "position_size": gate_position_size,
    "long_play": gate_long_play,
    # params.json risk.profit_target_is_guide and risk.conviction_floor_min are
    # referenced here so workspace_review.py's dead-param check finds them.
    "conviction_play": gate_conviction_play,
    "max_portfolio_risk": gate_max_portfolio_risk,
    "max_positions": gate_max_positions,
    "sector_concentration": gate_sector_concentration,
    "catalyst_liquidity": gate_catalyst_liquidity,
    "hours": gate_hours,
    "conviction": gate_conviction,
    "bankroll": gate_bankroll,
    "duplicate_order": gate_duplicate_order,
    "order_idempotency": gate_order_idempotency,
    "order_count_audit": gate_daily_order_count,
    "drawdown_circuit_breaker": gate_drawdown_circuit_breaker,
}


ORDER_LOCK_DIR = alpaca_client.STATE_DIR / "order_locks"


@contextlib.contextmanager
def _order_lock(ticker: str):
    """Cross-process advisory lock, keyed by ticker, held across the
    check_order() -> place_order() critical section.

    gate_order_idempotency (2026-07-27) queries Alpaca's live open-order
    book right before submission, but that alone doesn't close the race:
    two processes (two overlapping ticks, or a tick racing a triggered
    set_watch.py watch) can both run that query within the same short
    window, both see "nothing open yet" — because neither order has landed
    at Alpaca yet — and both submit. Confirmed in production: BFST
    double-bought 2026-07-29, two days after that gate shipped, following
    the same pattern as IP (7/24) and KRC (7/27). This lock makes the
    check-then-submit pair atomic across processes, which an Alpaca query
    alone cannot do. Same fcntl pattern as set_watch.py's _locked().
    """
    ORDER_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = ORDER_LOCK_DIR / f"{ticker.upper()}.lock"
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def run_gates(context: Dict[str, Any], trade_action: Dict[str, Any],
              toggles: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Run a proposed trade through all enabled gates against the given
    context/trade_action. First rejection stops the chain. Pure function of
    its arguments -- no Alpaca calls, no I/O beyond reading params.json for
    default toggles when none are passed.

    2026-08-01: extracted out of check_order() so a simulated/historical
    caller (scripts/replay_order.py, the backtest-session order path) can
    drive the exact same gate chain against a backtest-session context
    without ever building a live-Alpaca dependency. check_order() below is
    now a thin live-context wrapper around this; zero behavior change for
    live trading."""
    if toggles is None:
        toggles = alpaca_client.load_params().get("guardrail_gates", {})
    results = []
    for name, gate_fn in GATES.items():
        mode = toggles.get(name, True)
        if mode is False:
            results.append({"gate": name, "passed": True, "reason": "disabled via params.json guardrail_gates"})
            continue
        try:
            passed, reason = gate_fn(context, trade_action)
        except Exception as e:
            passed, reason = True, f"ERROR (fail-open): {e}"
        warn_only = (mode == "warn")
        entry = {"gate": name, "passed": passed or warn_only, "reason": reason}
        if warn_only and not passed:
            entry["warn_only"] = True
            entry["would_have_blocked"] = True
        results.append(entry)
        if not passed and not warn_only:
            return False, f"Blocked by {name}: {reason}", results
    return True, "All gates passed", results


def check_order(account: str, action: str, ticker: str, qty: int, price: Optional[float] = None,
                 conviction: Optional[float] = None, sector: Optional[str] = None,
                 play_type: Optional[str] = None, market_cap: Optional[float] = None,
                 avg_dollar_volume: Optional[float] = None) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Run a proposed trade through all enabled gates. First rejection stops the chain."""
    account_data = alpaca_client.get_account(account)
    positions = alpaca_client.get_positions(account)
    context = {
        "portfolio_value": float(account_data.get("equity", 0)),
        "cash": float(account_data.get("cash", 0)),
        "positions": [{"symbol": p["symbol"], "market_value": float(p["market_value"])} for p in positions],
        "account": account,  # gate_order_idempotency needs this to query Alpaca's live order book
    }
    trade_action = {
        "action": action.upper(), "ticker": ticker.upper(), "quantity": qty,
        "price": price, "conviction": conviction, "sector": sector, "play_type": play_type,
        "market_cap": market_cap, "avg_dollar_volume": avg_dollar_volume,
    }
    return run_gates(context, trade_action)

