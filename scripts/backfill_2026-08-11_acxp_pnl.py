#!/usr/bin/env python3
"""One-off backfill: correct ACXP realized_pnl (was $0.00 due to the SELL
path trusting an agent-supplied --price with zero reconciliation against
the real fill, fixed 2026-08-11 in the same commit as this script -- see
executor.py's SELL path). Real fill prices queried live from Alpaca via
Stan (this session has no Alpaca API keys):
  BUY  1 @ $1.53 (filled 2026-08-10T17:37:44Z)
  SELL 1 @ $1.50 (filled 2026-08-11T15:42:23.326728Z)
  order id 190c52f4-a74c-40f8-84e6-28bb54ab904e

Also replays bankroll_state/bankroll_history forward from the ceiling
value immediately before this trade (history index 34), using the real
recalc_ceiling() logic with the corrected pnl. Same shape as
backfill_2026-08-10_vsxy_flxs_clir_pnl.py.

DRY_RUN=True by default -- prints everything, writes nothing.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve()
sys.path.insert(0, str(REPO.parent.parent / "scripts"))
sys.path.insert(0, str(REPO.parent.parent))

import trader_db
import bankroll

DRY_RUN = "--apply" not in sys.argv

TICKER = "ACXP"
BUY_PRICE = 1.53
SELL_PRICE = 1.50
QTY = 1.0
CLOSED_AT = "2026-08-11T15:42:29.378388+00:00"
CLOSE_REASON = "trailing stop breached: -8.6% off peak .63, stop .52"

conn = trader_db.get_conn()
try:
    pnl = round((SELL_PRICE - BUY_PRICE) * QTY, 4)
    return_pct = round((SELL_PRICE - BUY_PRICE) / BUY_PRICE * 100, 4)
    print(f"=== Corrected positions.realized_pnl / realized_return_pct ===")
    print(f"{TICKER}: buy={BUY_PRICE} sell={SELL_PRICE} qty={QTY} -> pnl=${pnl:+.2f} ({return_pct:+.2f}%)")

    print("\n=== Bankroll state: current (before correction) ===")
    state = bankroll.read_bankroll()
    for k in ("ceiling", "growth_rate", "decay_rate", "target_profit_pct", "closed_trades",
              "wins", "losses", "net_pnl", "lifetime_trades", "lifetime_net_pnl",
              "lifetime_wins", "lifetime_losses"):
        print(f"  {k}: {state[k]}")
    print(f"  history (last 8):")
    for h in state["history"][-8:]:
        print(f"    {h}")

    # Target entry: index 34, "08/11 15:42 LOSS $+0.00 -> $735.92", the
    # single ACXP $0.00 entry. Ceiling before it (index 33's output) was
    # $735.92. index 35 (18:51 WIN $+1.26) is the one entry after it, real
    # and unchanged -- but it still needs to be undone-and-replayed too,
    # since its ceiling calc chains off entry 34's (now-corrected) ceiling
    # and its wins/lifetime_wins contribution is already baked into
    # `state`'s current aggregates alongside entry 34's. Undoing only
    # entry 34 and replaying both would double-count entry 35.
    idx = 34
    print(f"\n=== Target entry (and the one real entry after it) ===")
    print(f"  [{idx}] {state['history'][idx]}")
    print(f"  [{idx + 1}] {state['history'][idx + 1]}")

    pre_ceiling = 735.92  # ceiling immediately before index 34 (== index 33's output)
    pre_state = dict(state)
    pre_state["history"] = state["history"][:idx]
    pre_state["ceiling"] = pre_ceiling
    # Undo both entry 34 (LOSS $0.00) and entry 35 (WIN $+1.26):
    pre_state["closed_trades"] = state["closed_trades"] - 2
    pre_state["wins"] = state["wins"] - 1
    pre_state["losses"] = state["losses"] - 1
    pre_state["net_pnl"] = state["net_pnl"] - 0.0 - 1.26
    pre_state["lifetime_trades"] = state["lifetime_trades"] - 2
    pre_state["lifetime_wins"] = state["lifetime_wins"] - 1
    pre_state["lifetime_losses"] = state["lifetime_losses"] - 1
    pre_state["lifetime_net_pnl"] = state["lifetime_net_pnl"] - 0.0 - 1.26
    pre_state["lifetime_win_pnl_sum"] = state.get("lifetime_win_pnl_sum", 0.0) - 1.26
    pre_state["lifetime_loss_pnl_sum"] = state.get("lifetime_loss_pnl_sum", 0.0) - 0.0

    print(f"\n=== Reconstructed pre-ACXP state (after [{idx - 1}]) ===")
    for k in ("ceiling", "closed_trades", "wins", "losses", "lifetime_trades", "lifetime_wins", "lifetime_losses"):
        print(f"  {k}: {pre_state[k]}")

    replay_state = dict(pre_state)

    # ACXP (entry [34]): LOSS, -0.03. recalc_ceiling() auto-appends a
    # history entry using datetime.now() -- overwrite it with the real
    # historical timestamp immediately after, same as the VSXY/FLXS/CLIR
    # backfill did.
    bankroll.recalc_ceiling(replay_state, pnl, is_win=(pnl > 0))
    replay_state["history"][-1] = f"08/11 15:42 LOSS ${pnl:+.2f} → ${replay_state['ceiling']:.2f}"

    # Reinsert anything after [34] (index 35: the 18:51 WIN), replayed
    # unchanged with its own real pnl so it lands on top of the corrected
    # ceiling instead of the stale one.
    for i in range(idx + 1, len(state["history"])):
        h = state["history"][i]
        parts = h.split(" ")
        if len(parts) >= 5 and parts[3].startswith("$"):
            pnl_str = parts[3].replace("$", "").replace("+", "")
            replay_pnl = float(pnl_str)
            is_win = "WIN" in h
            bankroll.recalc_ceiling(replay_state, replay_pnl, is_win=is_win)
            ts = " ".join(parts[0:2])
            replay_state["history"][-1] = (
                f"{ts} {'WIN' if is_win else 'LOSS'} ${'+' if replay_pnl >= 0 else ''}{replay_pnl:.2f} → ${replay_state['ceiling']:.2f}"
            )

    print(f"\n=== Bankroll state: after correct replay ===")
    for k in ("ceiling", "growth_rate", "decay_rate", "target_profit_pct", "closed_trades",
              "wins", "losses", "net_pnl", "lifetime_trades", "lifetime_net_pnl",
              "lifetime_wins", "lifetime_losses"):
        print(f"  {k}: {replay_state[k]}")
    print(f"  history (last 8):")
    for h in replay_state["history"][-8:]:
        print(f"    {h}")

    if DRY_RUN:
        print("\n--- DRY RUN: nothing written. Re-run with --apply to commit. ---")
    else:
        print("\n--- APPLYING ---")
        conn.execute("""
            UPDATE positions SET realized_pnl = ?, realized_return_pct = ?,
            entry_price = ?, shares = ?, close_reason = ?, updated_at = datetime('now')
            WHERE ticker = ? AND status = 'closed' AND closed_at = ?
        """, (pnl, return_pct, BUY_PRICE, QTY, CLOSE_REASON, TICKER, CLOSED_AT))
        conn.commit()
        print(f"  positions row updated: {TICKER} entry={BUY_PRICE} qty={QTY} pnl={pnl} return={return_pct}%")
        bankroll.write_bankroll(replay_state)
        print("  bankroll_state / bankroll_history written")
finally:
    conn.close()
