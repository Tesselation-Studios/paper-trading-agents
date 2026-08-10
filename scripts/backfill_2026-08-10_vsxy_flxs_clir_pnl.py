#!/usr/bin/env python3
"""One-off backfill: correct VSXY/FLXS/CLIR realized_pnl (were $0.00 due
to the SELL exit-price timeout bug fixed 2026-08-10 in commit 65c44bc, 1s->5s).
Uses REAL Alpaca fill prices for both entry and exit legs.

Also replays bankroll_state/bankroll_history forward from the ceiling value
immediately before these 3 trades, using the real recalc_ceiling() logic
with corrected is_win/pnl values.

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

# Real fills pulled from Alpaca API on 2026-08-10:
#
# VSXY: 3 buys (scaled in), 1 sell
#   BUY 1 @ $87.95 (2026-07-29T15:38)
#   BUY 1 @ $90.87 (2026-07-29T18:57)
#   BUY 1 @ $89.42 (2026-08-04T14:10)
#   SELL 3 @ $98.00 (2026-08-10T13:35)
#   Avg entry: (87.95+90.87+89.42)/3 = $89.4133
#
# FLXS:
#   BUY 1 @ $74.00 (2026-07-31T18:19)
#   SELL 1 @ $76.22 (2026-08-04T13:33)
#
# CLIR (first round -- the DB row is from the first entry; the re-entry at
# $4.00->$3.75 on Aug 4-6 was a separate round never tracked in positions):
#   BUY 1 @ $4.40 (2026-08-04T13:53)
#   SELL 1 @ $3.96 (2026-08-04T13:56)

TRADES = [
    # ticker, buy_avg, sell_price, qty, closed_at, close_reason, corrected_entry
    ("VSXY", 89.4133, 98.00, 3.0,
     "2026-08-10T13:31:39.703325+00:00",
     "profit target +10.80%, bootstrap quick-exit, CHOPPY regime",
     89.41),
    ("FLXS", 74.00, 76.22, 1.0,
     "2026-08-04T13:32:21.099916+00:00",
     "bootstrap quick-exit: +3.00% P&L (fills: $74.00 -> $76.22), ceiling $702 < $1000 threshold",
     74.00),
    ("CLIR", 4.40, 3.96, 1.0,
     "2026-08-04T13:56:32.175257+00:00",
     "hard stop breach: -10.00% loss ($4.40 -> $3.96)",
     4.40),
]

# Bankroll history entry indices for these 3 trades:
# [18] 08/04 13:32 LOSS $+0.00 → $714.23  (FLXS)
# [19] 08/04 13:56 LOSS $+0.00 → $714.23  (CLIR)
# [26] 08/10 13:31 LOSS $+0.00 → $712.68  (VSXY)

conn = trader_db.get_conn()
try:
    print("=== Corrected positions.realized_pnl / realized_return_pct ===")
    corrections = []
    for ticker, buy, sell, qty, closed_at, close_reason, corrected_entry in TRADES:
        pnl = round((sell - buy) * qty, 4)
        return_pct = round((sell - buy) / buy * 100, 4)
        corrections.append((ticker, closed_at, close_reason, pnl, return_pct, qty, corrected_entry))
        print(f"{ticker}: buy={buy} sell={sell} qty={qty} -> pnl=${pnl:+.2f} ({return_pct:+.2f}%)")

    print("\n=== Bankroll state: current (before correction) ===")
    state = bankroll.read_bankroll()
    for k in ("ceiling", "growth_rate", "decay_rate", "target_profit_pct", "closed_trades",
              "wins", "losses", "net_pnl", "lifetime_trades", "lifetime_net_pnl",
              "lifetime_wins", "lifetime_losses"):
        print(f"  {k}: {state[k]}")
    print(f"  history (last 8):")
    for h in state["history"][-8:]:
        print(f"    {h}")

    # Verify the 3 target entries are where we expect them
    print("\n=== Target entries ===")
    for idx in [18, 19, 26]:
        print(f"  [{idx}] {state['history'][idx]}")

    # Reconstruct pre-trade state (before entry [18]):
    # The 3 bad entries [18], [19], [26] are all LOSS with $0.00 pnl
    # and no ceiling change (all decayed to same value since pnl=0).
    # Remove them: drop the last entries from [18] onward, then replay.
    # Actually, entries [20]-[25] happened AFTER [18],[19] but BEFORE [26].
    # We need to remove [18],[19],[26] and replay the correct values in
    # their chronological slots, keeping [20]-[25] unchanged in between.
    #
    # Simpler approach: roll back to before [18], re-insert [18] and [19]
    # with correct values, keep [20]-[25] unchanged, re-insert [26] with
    # correct value, and anything after [26] stays.
    #
    # Pre-[18] state: ceiling at [17] was 714.23
    # After the 3 bad 0.00 entries, ceiling was: 714.23 * decay^3 = 714.23
    # (since pnl=0, no growth component, just decay... but wait, each
    # recalc_ceiling applies decay AND growth. With pnl=0, ceiling *= (1-decay_rate).
    # Three decays with no growth would drop ceiling.)

    # Actually, the ceiling DIDN'T change for [18] and [19] ($714.23 -> $714.23).
    # That means the growth component (pnl-based) offset the decay exactly...
    # No, that's not how it works. With pnl=0, recalc_ceiling just decays.
    # Unless the decay rate is tiny at this point.

    # Let me just recalculate from pre-[18] state:
    # Cut off at [17], replay [18],[19] (corrected), keep [20]-[25], replay [26] (corrected)
    
    pre_state = dict(state)
    
    # Cut history at [17] (keep entries 0-17)
    pre_state["history"] = state["history"][:18]  # entries 0..17

    # Reverse-engineer the ceiling before the first bad trade.
    # Entry [17] ended at ceiling=714.23. Entry [18] with pnl=0.0, loss
    # just decayed (1 * (1-decay_rate)). But pre_state ceiling should
    # equal the ceiling from [17]'s output.
    pre_ceiling = 714.23  # from [17] output / [18] input

    # But first recalc_ceiling will apply decay again, so we need to
    # set the ceiling to the value BEFORE [17]'s decay was applied.
    # Wait, [17] ended at 714.23, and [18] STARTED from that ceiling
    # and then applied decay. So pre_state's ceiling should be 714.23.
    # Then when we replay [18] with real pnl, recalc_ceiling will
    # apply decay and growth to get the new ceiling.
    pre_state["ceiling"] = pre_ceiling

    # Fix counts: remove 3 losses, 3 closed trades
    pre_state["closed_trades"] = state["closed_trades"] - 3
    pre_state["losses"] = state["losses"] - 3
    pre_state["lifetime_trades"] = state["lifetime_trades"] - 3
    pre_state["lifetime_losses"] = state["lifetime_losses"] - 3
    # net_pnl, wins, lifetime_wins: unchanged (0 contribution)

    print(f"\n=== Reconstructed pre-FLXS state (after [17]) ===")
    for k in ("ceiling", "closed_trades", "losses", "lifetime_trades", "lifetime_losses"):
        print(f"  {k}: {pre_state[k]}")

    # Replay in order: [18]=FLXS, [19]=CLIR, keep [20]-[25], [26]=VSXY
    replay_state = dict(pre_state)

    # FLXS (entry [18]): WIN, +2.22
    bankroll.recalc_ceiling(replay_state, 2.22, is_win=True)
    # FLXS real time: Aug 4 13:32
    replay_state["history"][-1] = "08/04 13:32 WIN $+2.22 → " + replay_state["history"][-1].split("→ ")[-1]

    # CLIR (entry [19]): LOSS, -0.44
    bankroll.recalc_ceiling(replay_state, -0.44, is_win=False)
    replay_state["history"][-1] = "08/04 13:56 LOSS $-0.44 → " + replay_state["history"][-1].split("→ ")[-1]

    # Reinsert [20]-[25] unchanged (these are real trades that happened between CLIR and VSXY)
    for i in range(20, 26):
        if i < len(state["history"]):
            h = state["history"][i]
            # Parse and replay... actually these are real, correct trades.
            # We need to replay them with their real P&L values.
            # Extract P&L from each line.
            parts = h.split(" ")
            # Format: "08/04 14:22 WIN $+10.81 → $728.34"
            if len(parts) >= 5 and parts[3].startswith("$"):
                pnl_str = parts[3].replace("$", "").replace("+", "")
                pnl = float(pnl_str)
                is_win = "WIN" in h
                bankroll.recalc_ceiling(replay_state, pnl, is_win=is_win)
                # Preserve the original timestamp and format
                ts = " ".join(parts[0:2])
                new_ceiling = replay_state["history"][-1].split("→ ")[-1]
                replay_state["history"][-1] = f"{ts} {'WIN' if is_win else 'LOSS'} ${'+' if pnl >= 0 else ''}{pnl:.2f} → {new_ceiling}"

    # VSXY (entry [26]): WIN, +25.76
    bankroll.recalc_ceiling(replay_state, 25.76, is_win=True)
    replay_state["history"][-1] = "08/10 13:31 WIN $+25.76 → " + replay_state["history"][-1].split("→ ")[-1]

    # Reinsert anything after [26]
    for i in range(27, len(state["history"])):
        h = state["history"][i]
        parts = h.split(" ")
        if len(parts) >= 5 and parts[3].startswith("$"):
            pnl_str = parts[3].replace("$", "").replace("+", "")
            pnl = float(pnl_str)
            is_win = "WIN" in h
            bankroll.recalc_ceiling(replay_state, pnl, is_win=is_win)
            ts = " ".join(parts[0:2])
            new_ceiling = replay_state["history"][-1].split("→ ")[-1]
            replay_state["history"][-1] = f"{ts} {'WIN' if is_win else 'LOSS'} ${'+' if pnl >= 0 else ''}{pnl:.2f} → {new_ceiling}"

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
        for ticker, closed_at, close_reason, pnl, return_pct, qty, corrected_entry in corrections:
            conn.execute("""
                UPDATE positions SET realized_pnl = ?, realized_return_pct = ?,
                entry_price = ?, shares = ?, close_reason = ?, updated_at = datetime('now')
                WHERE ticker = ? AND status = 'closed' AND closed_at = ?
            """, (pnl, return_pct, corrected_entry, qty, close_reason, ticker, closed_at))
            conn.commit()
            print(f"  positions row updated: {ticker} entry={corrected_entry} qty={qty} pnl={pnl} return={return_pct}%")
        bankroll.write_bankroll(replay_state)
        print("  bankroll_state / bankroll_history written")
finally:
    conn.close()