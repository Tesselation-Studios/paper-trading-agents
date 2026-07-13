# Kairos — Momentum Trader

> **Role**: Primary momentum trader. Reads params from `strategies/params.json`.
> **Style**: Trend-following with RSI confirmation and regime-aware sizing.
> **Optimization**: Gradient descent + nightly sweep.

---

## Entry conditions (all must pass)

1. `momentum_signal == "BULLISH"` — price momentum above `momentum_threshold`
2. `conviction >= 0.20` — engine confidence in the signal
3. `position_count < max_positions` — not exceeding portfolio limit
4. `volume_pass == True` — volume filter passes (or extreme fear bypass)
5. NOT last trading day of the data window

## Exit conditions

- **Stop-loss**: price <= `stop_loss` price from signal report
- **Take-profit**: price >= `take_profit` price from signal report
- **Forced EOD**: last day of trading window — close all positions

## Position sizing

- `base_size_pct` of equity per position, multiplied by `regime_weight`
- `conviction_multiplier` applies when conviction is high
- Never exceed `max_positions` concurrent positions

## Bankroll

- Start: $35 limit
- ±1%/trade based on 20-trade rolling win rate
- All trades (live + sim) count toward win rate