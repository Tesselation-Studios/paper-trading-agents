# Stonks — Aggressive/Growth Trader

> **Role**: High-growth aggressive trader.
> **Style**: Strong momentum + high conviction, larger positions, tighter stops.
> **Optimization**: Gradient descent + nightly sweep.

---

## Entry conditions (all must pass)

1. `composite_signal > 0.4` — stronger signal threshold than Kairos
2. `conviction >= 0.30` — higher bar for entry
3. `position_count < max_positions`
4. `momentum_signal == "BULLISH"` — momentum confirmation required
5. NOT last trading day

## Exit conditions

- Stop-loss hit (tighter: 3% vs 5% for Kairos)
- Take-profit hit (15% — same as Kairos)
- If momentum drops below 0 and position is underwater
- Forced EOD close on last day

## Position sizing

- `base_size_pct * 1.2` (larger — aggressive style)
- Full conviction multiplier always applies
- Max 3 concurrent positions (fewer, bigger bets)

## Bankroll

- Start: $35 limit
- ±1%/trade based on 20-trade rolling win rate