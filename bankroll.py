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
from datetime import datetime, timezone
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

    state["history"] = []
    in_history = False
    for line in text.splitlines():
        if line.strip() == "## History":
            in_history = True
            continue
        if in_history and line.strip().startswith("-- "):
            state["history"].append(line.strip())

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
    args = parser.parse_args()

    state = read_bankroll()

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