#!/usr/bin/env python3
"""One-off backfill: correct ZBRA/DXCM/OOMA's realized_pnl (were $0.00 due
to the SELL exit_price bug fixed 2026-08-03) using REAL Alpaca fill prices
for both entry and exit legs (their recorded entry_price in `positions`
also didn't match the real buy fill -- separate, smaller issue on the BUY
side, not something this script tries to fix generally, just accounted for
here so the corrected pnl reflects true economics for these 3 trades).

Also replays bankroll_state/bankroll_history forward from the ceiling value
immediately before these 3 trades, using the real recalc_ceiling() logic
with is_win=True and the corrected pnl, since nothing has closed since
(confirmed: these are still the last 3 bankroll_history rows).

DRY_RUN=True by default -- prints everything, writes nothing.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve()
sys.path.insert(0, "/home/openclaw/.openclaw/workspace-trader-stonks/scripts")
sys.path.insert(0, "/home/openclaw/.openclaw/workspace-trader-stonks")

import trader_db
import bankroll

DRY_RUN = "--apply" not in sys.argv  # already applied 2026-08-03, kept for audit trail -- do not re-run

# Real fills pulled from Alpaca get_closed_orders() on 2026-08-03.
TRADES = [
    # ticker, buy_fill, sell_fill, qty, closed_at (from positions table, unchanged), close_reason (unchanged)
    ("ZBRA", 282.53, 298.09, 1.0,
     "2026-08-03T13:36:29.216940+00:00",
     "bootstrap quick-exit: +5.33% above 5% trigger, ceiling 79"),
    ("DXCM", 82.08, 85.76, 1.0,
     "2026-08-03T13:36:29.996366+00:00",
     "bootstrap quick-exit: +5.28% above 5% trigger, ceiling 79"),
    ("OOMA", 21.19, 22.40, 1.0,
     "2026-08-03T13:46:28.889406+00:00",
     "bootstrap quick-exit: return above 5% trigger, bankroll ceiling $665 < $1000 threshold"),
]

conn = trader_db.get_conn()
try:
    print("=== Corrected positions.realized_pnl / realized_return_pct ===")
    corrections = []
    for ticker, buy, sell, qty, closed_at, close_reason in TRADES:
        pnl = round((sell - buy) * qty, 4)
        return_pct = round((sell - buy) / buy * 100, 4)
        corrections.append((ticker, closed_at, close_reason, pnl, return_pct))
        print(f"{ticker}: buy={buy} sell={sell} qty={qty} -> pnl=${pnl:+.2f} ({return_pct:+.2f}%)")

    print("\n=== Bankroll state: current (before correction) ===")
    state = bankroll.read_bankroll()
    for k in ("ceiling", "growth_rate", "decay_rate", "target_profit_pct", "closed_trades",
              "wins", "losses", "net_pnl", "lifetime_trades", "lifetime_net_pnl",
              "lifetime_wins", "lifetime_losses"):
        print(f"  {k}: {state[k]}")
    print(f"  history (last 5): {state['history'][-5:]}")

    # Reconstruct pre-ZBRA state. All 3 bad events had pnl=0.0, is_win=False,
    # so: ceiling was decayed 3x (invert by dividing out 3 decay factors),
    # closed_trades/losses/lifetime_trades/lifetime_losses each +3 too many,
    # wins/lifetime_wins/net_pnl/lifetime_net_pnl unaffected (0 contribution).
    decay_factor = (1 - state["decay_rate"]) ** 3
    pre_ceiling = state["ceiling"] / decay_factor

    pre_state = dict(state)
    pre_state["ceiling"] = pre_ceiling
    pre_state["closed_trades"] = state["closed_trades"] - 3
    pre_state["losses"] = state["losses"] - 3
    pre_state["lifetime_trades"] = state["lifetime_trades"] - 3
    pre_state["lifetime_losses"] = state["lifetime_losses"] - 3
    # net_pnl / lifetime_net_pnl / wins / lifetime_wins: unchanged (0 contribution, no wins)
    # history: drop the last 3 entries (the wrong ZBRA/DXCM/OOMA lines)
    pre_state["history"] = state["history"][:-3]

    print("\n=== Reconstructed pre-ZBRA state ===")
    for k in ("ceiling", "closed_trades", "losses", "lifetime_trades", "lifetime_losses"):
        print(f"  {k}: {pre_state[k]}")

    # Replay recalc_ceiling() with the REAL logic, in real chronological
    # order, using the corrected pnl/is_win for each trade. recalc_ceiling()
    # stamps history with datetime.now() -- overwrite that stamp with the
    # trade's real closed_at afterward so the audit trail shows when the
    # trade actually happened, not when this backfill ran.
    replay_state = pre_state
    for ticker, closed_at, close_reason, pnl, return_pct in corrections:
        bankroll.recalc_ceiling(replay_state, pnl, is_win=(pnl > 0))
        # closed_at is UTC ISO, matching recalc_ceiling's own "%m/%d %H:%M" UTC stamp
        from datetime import datetime
        real_ts = datetime.fromisoformat(closed_at).strftime("%m/%d %H:%M")
        last = replay_state["history"][-1]
        _, rest = last.split(" ", 1)  # drop the fake "now" date token
        _, rest = rest.split(" ", 1)  # drop the fake "now" time token
        replay_state["history"][-1] = f"{real_ts} {rest}"

    print("\n=== Bankroll state: after correct replay ===")
    for k in ("ceiling", "growth_rate", "decay_rate", "target_profit_pct", "closed_trades",
              "wins", "losses", "net_pnl", "lifetime_trades", "lifetime_net_pnl",
              "lifetime_wins", "lifetime_losses"):
        print(f"  {k}: {replay_state[k]}")
    print(f"  history (last 5): {replay_state['history'][-5:]}")

    if DRY_RUN:
        print("\n--- DRY RUN: nothing written. Re-run with --apply to commit. ---")
    else:
        print("\n--- APPLYING ---")
        for ticker, closed_at, close_reason, pnl, return_pct in corrections:
            trader_db.close_position(
                conn, ticker=ticker, closed_at=closed_at, close_reason=close_reason,
                realized_pnl=pnl, realized_return_pct=return_pct,
            )
            print(f"  positions row updated: {ticker}")
        bankroll.write_bankroll(replay_state)
        print("  bankroll_state / bankroll_history written")
finally:
    conn.close()
