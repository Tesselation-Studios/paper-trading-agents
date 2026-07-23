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
from datetime import date, datetime, timezone
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────
BANKROLL_FILE = Path(__file__).parent / "bankroll.md"
STARTING_CASH = 10_000.00

FLOOR = 50.00              # absolute minimum per tick
STARTING_CEILING = 50.00   # start tiny, scale up
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
AHEAD_PACE_THRESHOLD = 1.5      # equity/starting_capital ratio considered "a real lead"
AHEAD_PACE_MULTIPLIER = 0.85    # dampener once meaningfully ahead
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
    """Boost if behind the starting line, dampen once meaningfully ahead
    (protect a real lead) -- neutral in between. Not opponent-relative
    (no live standings for neko-chan/friends exist yet), just relative to
    Stan's own starting capital."""
    if starting_capital <= 0:
        return 1.0
    ratio = current_equity / starting_capital
    if ratio < 1.0:
        return BEHIND_PACE_MULTIPLIER
    if ratio >= AHEAD_PACE_THRESHOLD:
        return AHEAD_PACE_MULTIPLIER
    return 1.0


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


def read_bankroll() -> dict:
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
    }
    if not BANKROLL_FILE.exists():
        return state

    text = BANKROLL_FILE.read_text()

    m = re.search(r"Ceiling:\s*\$?([\d.]+)", text)
    if m:
        state["ceiling"] = max(FLOOR, float(m.group(1)))

    m = re.search(r"Growth/decay rate:\s*([\d.]+)\s*/\s*([\d.]+)", text)
    if m:
        state["growth_rate"] = float(m.group(1))
        state["decay_rate"] = float(m.group(2))

    m = re.search(r"Target profit:\s*([\d.]+)%", text)
    if m:
        state["target_profit_pct"] = float(m.group(1))

    m = re.search(r"Closed trades this session:\s*(\d+)", text)
    if m:
        state["closed_trades"] = int(m.group(1))

    m = re.search(r"Wins:\s*(\d+)\s*\|?\s*Losses:\s*(\d+)", text)
    if m:
        state["wins"] = int(m.group(1))
        state["losses"] = int(m.group(2))

    m = re.search(r"Net:\s*([+-]?[\d.]+)%", text)
    if m:
        state["net_pnl"] = float(m.group(1))

    m = re.search(r"Lifetime trades:\s*(\d+)", text)
    if m:
        state["lifetime_trades"] = int(m.group(1))

    m = re.search(r"Lifetime net PnL:\s*\$?([+-]?[\d.]+)", text)
    if m:
        state["lifetime_net_pnl"] = float(m.group(1))

    state["history"] = []
    in_history = False
    for line in text.splitlines():
        if line.strip() == "## History":
            in_history = True
            continue
        if in_history and line.strip().startswith("-- "):
            state["history"].append(line.strip().removeprefix("-- "))

    return state


def write_bankroll(state: dict):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Bankroll — Stonks",
        "",
        f"Ceiling: ${state['ceiling']:.2f}",
        f"Growth/decay rate: {state['growth_rate']:.2f} / {state['decay_rate']:.2f}",
        f"Target profit: {state['target_profit_pct']:.2f}%",
        f"Closed trades this session: {state['closed_trades']}",
        f"Wins: {state['wins']} | Losses: {state['losses']}",
        f"Net: {state['net_pnl']:+.2f}%",
        f"Total deployed: ${state['total_deployed']:.2f}",
        f"Lifetime trades: {state['lifetime_trades']}",
        f"Lifetime net PnL: ${state['lifetime_net_pnl']:+.2f}",
        f"Updated: {now}",
        "",
        "## History",
    ]
    for entry in state["history"][-50:]:
        lines.append(f"-- {entry}")
    if not state["history"]:
        lines.append("-- (no closed trades yet)")

    BANKROLL_FILE.write_text("\n".join(lines) + "\n")


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

    # Dynamic calibration: growth rate accelerates with consistent wins
    if trade_count >= 10 and state["wins"] > 0:
        win_rate = state["wins"] / trade_count
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
# Tiers recalibrated against today's real starting point (ceiling ~$50-51
# right now), not the stale numbers TOOLS.md had.
UNIVERSE_MAX_PRICE_TIERS = [
    (100.0, 50.0),    # ceiling < $100 -> current $1-$50 universe, unchanged
    (300.0, 75.0),
    (750.0, 150.0),
    (MAX_CEILING, 300.0),
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


def format_output(state: dict) -> str:
    return (
        f"Ceiling: ${state['ceiling']:.2f} | "
        f"Trades: {state['closed_trades']} "
        f"(W:{state['wins']} L:{state['losses']}) | "
        f"Net: {state['net_pnl']:+.2f}% | "
        f"Target: {state['target_profit_pct']:.1f}% | "
        f"Growth: {state['growth_rate']:.2f}/decay"
    )


def main():
    parser = argparse.ArgumentParser(description="Stonks Bankroll")
    parser.add_argument("--win", type=float, help="Record a winning trade")
    parser.add_argument("--loss", type=float, help="Record a losing trade")
    parser.add_argument("--reset", action="store_true", help="Reset to defaults")
    parser.add_argument("--set-ceiling", type=float, help="Manual ceiling override")
    parser.add_argument("--tier", action="store_true", help="Show real unlock-tier status")
    args = parser.parse_args()

    state = read_bankroll()

    if args.tier:
        print(format_tier(tier_status(state)))
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