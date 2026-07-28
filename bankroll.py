#!/usr/bin/env python3
"""
bankroll.py — Self-calibrating per-tick risk ceiling.

Starts aggressive, grows with wins, shrinks with losses.
Goal: deploy majority of capital when opportunities exist.

Usage:
    python3 bankroll.py                   # print current ceiling
    python3 bankroll.py --win 12.50       # record a $12.50 win, recalc
    python3 bankroll.py --loss 8.00       # record an $8.00 loss, recalc
    python3 bankroll.py --reset           # reset to defaults
    python3 bankroll.py --set-ceiling 500 # manual override
"""

import argparse
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import trader_db  # noqa: E402

# ── Config ─────────────────────────────────────────────────────────────────
STARTING_CASH = 10_000.00

FLOOR = 50.00              # absolute minimum per tick
# 2026-07-27: was $50 -- with a $10,414 account and max_position_pct at 6%
# ($625), the ceiling had become the dominant constraint by a 12x margin,
# blocking real qualified candidates outright on price alone (e.g. GL at
# $173/share couldn't even buy 1 share). Raf: bump the start, keep the
# win/loss-earned growth mechanism intact rather than removing it.
STARTING_CEILING = 700.00  # per-tick spending budget; grows/decays with win/loss track record
MAX_CEILING = 2000.00       # hard cap
GROWTH_RATE = 0.02          # +2% per win
DECAY_RATE = 0.01           # -1% per loss
TARGET_PROFIT_PCT = 0.01    # 1% of position = take-profit target

# ── Competition mode (2026-07-23) ────────────────────────────────────────────
# Goal is most money by the deadline, not risk-adjusted return -- see
# SOUL.md. competition_multiplier() layers time-to-deadline and
# ahead/behind-pace awareness onto the win/loss ceiling above; it does not
# replace it. Numbers here are a real strategic dial, reviewed with Raf
# before shipping, not guessed -- easy to retune since they're isolated
# constants, not logic scattered through the function.
COMPETITION_END = date(2026, 12, 31)
ENDGAME_WINDOW_DAYS = 60      # ramp starts this many days out from the deadline
ENDGAME_MAX_MULTIPLIER = 1.4  # multiplier at day zero
BEHIND_PACE_MULTIPLIER = 1.15   # equity below starting capital

# 2026-07-24: replaces the old two-zone step function (neutral 1.0x-1.5x,
# flat 0.85x dampener beyond). Raf's framing: keep leaning in to extend the
# lead through ordinary outperformance — only pull back hard once the lead
# is big enough to protect outright. LEAD_PROTECT_THRESHOLD (2.0x = doubled
# the starting stake) is Raf's own example, "not being literal" — a round,
# retunable anchor for "big enough to bank it," not a precise line. Ramps
# continuously between 1.0x (breakeven) and LEAD_PROTECT_THRESHOLD rather
# than jumping, so there's no single trade that suddenly flips the ceiling.
LEAD_PROTECT_THRESHOLD = 2.0      # equity/starting_capital ratio considered "the lead is big enough to bank it"
LEAN_IN_MAX_MULTIPLIER = 1.3      # ceiling multiplier just below the protect threshold
LEAD_PROTECT_MULTIPLIER = 0.7     # dampener once at/beyond the protect threshold
COMBINED_MULTIPLIER_BOUNDS = (0.7, 1.5)  # clamp so the two factors can't compound into something extreme


def days_remaining(today: date = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    return max(0, (COMPETITION_END - today).days)


def endgame_factor(today: date = None) -> float:
    """1.0x until the final ENDGAME_WINDOW_DAYS, then ramps linearly up to
    ENDGAME_MAX_MULTIPLIER by the deadline -- matches COMPETITION.md's own
    "endgame, max aggression" framing: willing to swing bigger late if the
    number isn't where it needs to be."""
    remaining = days_remaining(today)
    if remaining >= ENDGAME_WINDOW_DAYS:
        return 1.0
    progress = 1 - (remaining / ENDGAME_WINDOW_DAYS)  # 0 at window start -> 1 at deadline
    return 1.0 + progress * (ENDGAME_MAX_MULTIPLIER - 1.0)


def performance_factor(current_equity: float, starting_capital: float = STARTING_CASH) -> float:
    """Boost if behind the starting line. Between breakeven and
    LEAD_PROTECT_THRESHOLD, keep leaning in -- ordinary outperformance
    (including gains sitting unrealized in a held winner, since
    current_equity is total equity, not just closed-trade P&L) is treated
    as validated judgment, not a reason to get cautious. Only dampens hard
    once the lead is big enough to bank (>= LEAD_PROTECT_THRESHOLD)."""
    if starting_capital <= 0:
        return 1.0
    ratio = current_equity / starting_capital
    if ratio < 1.0:
        return BEHIND_PACE_MULTIPLIER
    if ratio >= LEAD_PROTECT_THRESHOLD:
        return LEAD_PROTECT_MULTIPLIER
    # 1.0x (breakeven) -> LEAN_IN_MAX_MULTIPLIER (just under the protect threshold)
    progress = (ratio - 1.0) / (LEAD_PROTECT_THRESHOLD - 1.0)
    return 1.0 + progress * (LEAN_IN_MAX_MULTIPLIER - 1.0)


def competition_multiplier(current_equity: float, today: date = None,
                            starting_capital: float = STARTING_CASH) -> float:
    combined = endgame_factor(today) * performance_factor(current_equity, starting_capital)
    lo, hi = COMBINED_MULTIPLIER_BOUNDS
    return max(lo, min(hi, combined))


def effective_ceiling(state: dict, current_equity: float, today: date = None) -> float:
    """The real per-tick spending ceiling after competition-mode
    adjustment -- still hard-capped at MAX_CEILING regardless of how large
    the multiplier gets."""
    multiplier = competition_multiplier(current_equity, today)
    return min(MAX_CEILING, state["ceiling"] * multiplier)


def read_bankroll(db_path: Path = None) -> dict:
    """Reads from trader_db.py's bankroll_state (singleton row) +
    bankroll_history (last 50 rows). Migrated 2026-07-28 from a
    regex-parsed bankroll.md -- history is reconstructed as the same
    formatted-string list (f"{ts} {label} ${pnl:+.2f} -> ${ceiling:.2f}")
    recalc_ceiling()/expectancy_trend() already expect, so neither of
    those needed to change, only the I/O boundary."""
    state = {
        "ceiling": STARTING_CEILING,
        "growth_rate": GROWTH_RATE,
        "decay_rate": DECAY_RATE,
        "target_profit_pct": TARGET_PROFIT_PCT,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "net_pnl": 0.0,
        "total_deployed": 0.0,
        "history": [],
        "lifetime_trades": 0,
        "lifetime_net_pnl": 0.0,
        "lifetime_wins": 0,
        "lifetime_losses": 0,
    }
    conn = trader_db.get_conn(db_path)
    try:
        db_state = trader_db.get_bankroll_state(conn)
        if db_state:
            state["ceiling"] = max(FLOOR, db_state["ceiling"])
            state["growth_rate"] = db_state["growth_rate"]
            state["decay_rate"] = db_state["decay_rate"]
            state["target_profit_pct"] = db_state["target_profit_pct"]
            state["closed_trades"] = db_state["closed_trades_session"]
            state["wins"] = db_state["wins_session"]
            state["losses"] = db_state["losses_session"]
            state["net_pnl"] = db_state["net_pnl_session"]
            state["total_deployed"] = db_state["total_deployed_session"]
            state["lifetime_trades"] = db_state["lifetime_trades"]
            state["lifetime_net_pnl"] = db_state["lifetime_net_pnl"]
            state["lifetime_wins"] = db_state["lifetime_wins"]
            state["lifetime_losses"] = db_state["lifetime_losses"]

        history_rows = trader_db.get_bankroll_history(conn, limit=50)
        state["history"] = [
            f"{r['timestamp']} {r['label']} ${r['pnl']:+.2f} → ${r['ceiling_after']:.2f}"
            for r in reversed(history_rows)
        ]
    finally:
        conn.close()
    return state


_HISTORY_ENTRY_RE = re.compile(r"(\S+ \S+) (WIN|LOSS) \$([+-]?[\d.]+) → \$([\d.]+)")


def write_bankroll(state: dict, db_path: Path = None):
    """Upserts the singleton bankroll_state row, then fully replaces
    bankroll_history (delete+reinsert from state['history'][-50:]) --
    same bounded-list-replace pattern discovery_db.py's
    upsert_universe_snapshot() already uses. Entries that don't match the
    WIN/LOSS format (e.g. a "reset to defaults" marker) aren't
    representable in the structured schema and are dropped -- cosmetic
    only, never real financial data."""
    conn = trader_db.get_conn(db_path)
    try:
        trader_db.upsert_bankroll_state(
            conn, ceiling=state["ceiling"], growth_rate=state["growth_rate"],
            decay_rate=state["decay_rate"], target_profit_pct=state["target_profit_pct"],
            closed_trades_session=state["closed_trades"], wins_session=state["wins"],
            losses_session=state["losses"], net_pnl_session=state["net_pnl"],
            total_deployed_session=state.get("total_deployed", 0.0),
            lifetime_trades=state.get("lifetime_trades", 0),
            lifetime_net_pnl=state.get("lifetime_net_pnl", 0.0),
            lifetime_wins=state.get("lifetime_wins", 0),
            lifetime_losses=state.get("lifetime_losses", 0),
        )
        with conn:
            conn.execute("DELETE FROM bankroll_history")
            for entry in state["history"][-50:]:
                m = _HISTORY_ENTRY_RE.match(entry)
                if not m:
                    continue
                timestamp, label, pnl, ceiling_after = m.groups()
                conn.execute(
                    "INSERT INTO bankroll_history (timestamp, label, pnl, ceiling_after) VALUES (?, ?, ?, ?)",
                    (timestamp, label, float(pnl), float(ceiling_after)),
                )
    finally:
        conn.close()


def record_deployment(state: dict, cost: float):
    """Called on a successful BUY -- tracks cumulative $ actually put to
    work this session. Session-scoped (zeroed by --reset), same as
    net_pnl/closed_trades. Nothing called this before 2026-07-27; the field
    was write-only (printed but never read back in, and never incremented) --
    see the read_bankroll()/write_bankroll() fix above."""
    state["total_deployed"] = state.get("total_deployed", 0.0) + max(0.0, cost)


def recalc_ceiling(state: dict, pnl: float, is_win: bool):
    trade_count = state["closed_trades"] + 1

    if is_win:
        state["ceiling"] = min(MAX_CEILING, state["ceiling"] * (1 + state["growth_rate"]))
        state["wins"] += 1
    else:
        state["ceiling"] = max(FLOOR, state["ceiling"] * (1 - state["decay_rate"]))
        state["losses"] += 1

    state["closed_trades"] = trade_count
    state["net_pnl"] += pnl
    state["lifetime_trades"] = state.get("lifetime_trades", 0) + 1
    state["lifetime_net_pnl"] = state.get("lifetime_net_pnl", 0.0) + pnl
    if is_win:
        state["lifetime_wins"] = state.get("lifetime_wins", 0) + 1
    else:
        state["lifetime_losses"] = state.get("lifetime_losses", 0) + 1

    # Dynamic calibration: growth rate accelerates with consistent wins.
    # Uses lifetime_wins/lifetime_trades (survive --reset), not the session
    # counters above (wins/closed_trades) -- those get wiped by --reset,
    # which was silently resetting this calibration's evidence back to zero
    # every time (2026-07-27 fix).
    lifetime_trades = state.get("lifetime_trades", 0)
    lifetime_wins = state.get("lifetime_wins", 0)
    if lifetime_trades >= 10 and lifetime_wins > 0:
        win_rate = lifetime_wins / lifetime_trades
        if win_rate > 0.55:
            bonus = min(0.03, (win_rate - 0.55) * 0.15)
            state["growth_rate"] = round(min(0.08, GROWTH_RATE + bonus), 4)
        elif win_rate < 0.45:
            state["growth_rate"] = round(max(0.01, GROWTH_RATE - 0.003), 4)

    # Target profit shrinks as ceiling grows (take smaller % on bigger bets)
    if state["ceiling"] > 500:
        state["target_profit_pct"] = round(max(0.5, 1.0 - (state["ceiling"] - 500) * 0.0005), 2)
    else:
        state["target_profit_pct"] = TARGET_PROFIT_PCT

    label = "WIN" if is_win else "LOSS"
    now = datetime.now(timezone.utc).strftime("%m/%d %H:%M")
    state["history"].append(f"{now} {label} ${pnl:+.2f} → ${state['ceiling']:.2f}")


# Ceiling -> universe.max_price override, replacing TOOLS.md's old
# experience.json.peak_ceiling milestone table (2026-07-23: found stuck at
# its $50 starting value since creation, milestones_unlocked always empty
# — dead, prose-only, never actually fired). This is the one real,
# mechanized connection between bankroll growth and what Stan even looks
# at, not just position sizing — mechanizes strategy.md's Growth
# Trajectory section ("as real track record accumulates... sanctioned to
# widen toward larger-cap names"), which was previously unenforced prose.
#
# 2026-07-24: first tier widened $50 -> $500 on real evidence, not track
# record — a 250-ticker random-sample backtest (not the curated held/
# watchlist names) found mid-cap $50-500 meaningfully outperforms $1-50 on
# the identical strategy (mean Sharpe +0.014 vs -0.455, 56.5% vs 38.8%
# positive). This is a signal-quality unlock, distinct from the
# risk-capacity reasoning behind the rest of the ladder — small-caps trade
# on thin retail flow with little sustained momentum, so this mechanical
# RSI/MACD approach fares worse there regardless of bankroll size.
# max_position_pct (6%) still caps dollar risk per position the same way
# regardless of price — a pricier stock just gets fewer shares, same
# "small position size, wide diversification" philosophy, not a bigger bet.
# Remaining tiers rebased proportionally (10x, matching the first jump) to
# stay monotonic — those are still the original risk-capacity-based shape,
# not independently evidenced the way the first tier now is.
UNIVERSE_MAX_PRICE_TIERS = [
    (100.0, 500.0),    # ceiling < $100 -> widened 2026-07-24 (see above)
    (300.0, 750.0),
    (750.0, 1500.0),
    (MAX_CEILING, 3000.0),
]


def universe_max_price_for_ceiling(ceiling: float) -> float:
    for threshold, max_price in UNIVERSE_MAX_PRICE_TIERS:
        if ceiling < threshold:
            return max_price
    return UNIVERSE_MAX_PRICE_TIERS[-1][1]


# Unlock tiers (2026-07-23) -- replaces experience.json's current_level /
# milestones_unlocked, which nothing ever read and had already drifted from
# real trade counts (prose-agent-maintained, same dead-field pattern as the
# old peak_ceiling table). Thresholds adapted from
# paper-trading-rebuild/COMPETITION.md's Phase 1-4 unlock schedule. Gate is
# evaluated live each call, not unlocked-once-and-forgotten: expectancy can
# go negative again after a cold streak, and the tier reflects that.
UNLOCK_TIERS = [
    (1, "Stocks", 0),
    (2, "Shorting", 30),
    (3, "Crypto", 60),
    (4, "Options / leveraged ETFs / forex", 90),
]


def tier_status(state: dict) -> dict:
    """Real current unlock tier + progress toward the next one, computed from
    state['lifetime_trades'] / state['lifetime_net_pnl'] (both updated by
    executor.close_trade_outcome() on every real SELL -- not agent-reported).
    Deliberately separate from closed_trades/net_pnl, which are session-scoped
    and cleared by --reset; lifetime_* survives resets so the unlock clock
    can't be silently wiped by a ceiling reset."""
    trades = state.get("lifetime_trades", 0)
    net_pnl = state.get("lifetime_net_pnl", 0.0)
    expectancy = net_pnl / trades if trades else 0.0

    current = UNLOCK_TIERS[0]
    for tier in UNLOCK_TIERS:
        _, _, trades_needed = tier
        if trades >= trades_needed and (trades_needed == 0 or expectancy > 0):
            current = tier

    result = {
        "tier": current[0],
        "tier_name": current[1],
        "trades": trades,
        "expectancy": round(expectancy, 2),
    }

    next_tier = next((t for t in UNLOCK_TIERS if t[0] == current[0] + 1), None)
    if next_tier:
        _, next_name, next_trades_needed = next_tier
        result["next_tier"] = next_name
        result["trades_to_next"] = max(0, next_trades_needed - trades)
        result["expectancy_positive"] = expectancy > 0

    return result


def format_tier(status: dict) -> str:
    line = (f"Tier {status['tier']}/4: {status['tier_name']} | "
            f"{status['trades']} trades | "
            f"Expectancy: ${status['expectancy']:+.2f}/trade")
    if "next_tier" in status:
        blockers = []
        if status["trades_to_next"] > 0:
            blockers.append(f"{status['trades_to_next']} more trades")
        if not status["expectancy_positive"]:
            blockers.append("needs positive expectancy")
        blocker_text = " and ".join(blockers) if blockers else "ready"
        line += f" | Next: {status['next_tier']} ({blocker_text})"
    else:
        line += " | Max tier reached"
    return line


def expectancy_trend(state: dict, window: int = 10) -> str:
    """Compares average $ P&L of the last `window` trades (from bankroll.md's
    own rolling history log, which keeps up to 50) against lifetime
    expectancy -- a real signal from real logged outcomes, not a fabricated
    metric. Note: history is the same session-scoped log as closed_trades
    (cleared by --reset), so "recent" here means "since the last reset",
    not always a true last-N-trades window -- good enough for a directional
    read, not a precise one."""
    pnls = []
    for entry in state.get("history", [])[-window:]:
        m = re.search(r"(?:WIN|LOSS) \$([+-]?\d+\.\d+)", entry)
        if m:
            pnls.append(float(m.group(1)))
    if not pnls:
        return "no recent data"

    recent_avg = sum(pnls) / len(pnls)
    lifetime_trades = state.get("lifetime_trades", 0)
    lifetime_avg = (state.get("lifetime_net_pnl", 0.0) / lifetime_trades) if lifetime_trades else 0.0
    diff = recent_avg - lifetime_avg

    if abs(diff) < 0.5:
        direction = "flat"
    elif diff > 0:
        direction = "improving"
    else:
        direction = "declining"
    return f"{direction} (recent ${recent_avg:+.2f}/trade vs lifetime ${lifetime_avg:+.2f}/trade)"


def format_competition_status(state: dict, today: date = None) -> str:
    """Combined competition-mode context for stonks-evolution-batch: real
    tier progress, expectancy direction, and time pressure -- informational
    only, does NOT relax the Sharpe/split-window evidence bar for actual
    strategy changes (see skills/evolution-proposals.md)."""
    return "\n".join([
        format_tier(tier_status(state)),
        f"Trend: {expectancy_trend(state)}",
        f"Days to deadline (12/31/26): {days_remaining(today)}",
    ])


def format_output(state: dict) -> str:
    return (
        f"Ceiling: ${state['ceiling']:.2f} | "
        f"Trades: {state['closed_trades']} "
        f"(W:{state['wins']} L:{state['losses']}) | "
        f"Net: {state['net_pnl']:+.2f}% | "
        f"Deployed: ${state.get('total_deployed', 0.0):.2f} | "
        f"Target: {state['target_profit_pct']:.1f}% | "
        f"Growth: {state['growth_rate']:.2f}/decay "
        f"(lifetime {state.get('lifetime_wins', 0)}W/{state.get('lifetime_losses', 0)}L)"
    )


def main():
    parser = argparse.ArgumentParser(description="Stonks Bankroll")
    parser.add_argument("--win", type=float, help="Record a winning trade")
    parser.add_argument("--loss", type=float, help="Record a losing trade")
    parser.add_argument("--reset", action="store_true", help="Reset to defaults")
    parser.add_argument("--set-ceiling", type=float, help="Manual ceiling override")
    parser.add_argument("--tier", action="store_true", help="Show real unlock-tier status")
    parser.add_argument("--competition", action="store_true",
                         help="Full competition status: tier + expectancy trend + days remaining")
    args = parser.parse_args()

    state = read_bankroll()

    if args.tier:
        print(format_tier(tier_status(state)))
        return

    if args.competition:
        print(format_competition_status(state))
        return

    if args.reset:
        state = read_bankroll()
        state["ceiling"] = STARTING_CEILING
        state["growth_rate"] = GROWTH_RATE
        state["decay_rate"] = DECAY_RATE
        state["closed_trades"] = 0
        state["wins"] = 0
        state["losses"] = 0
        state["net_pnl"] = 0.0
        state["total_deployed"] = 0.0
        state["history"] = ["-- reset to defaults"]
        write_bankroll(state)
        print(f"Bankroll reset to ${STARTING_CEILING:.2f} ceiling")
        return

    if args.set_ceiling is not None:
        state["ceiling"] = max(FLOOR, min(MAX_CEILING, args.set_ceiling))
        write_bankroll(state)
        print(f"Ceiling set to $%.2f" % state["ceiling"])
        return

    if args.win is not None:
        recalc_ceiling(state, args.win, is_win=True)
        write_bankroll(state)
        print(f"Win recorded. Ceiling: ${state['ceiling']:.2f}")
        print(format_output(state))
        return

    if args.loss is not None:
        recalc_ceiling(state, -abs(args.loss), is_win=False)
        write_bankroll(state)
        print(f"Loss recorded. Ceiling: ${state['ceiling']:.2f}")
        print(format_output(state))
        return

    print(format_output(state))


if __name__ == "__main__":
    main()