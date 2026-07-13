# Aldridge — Value/Mean-Reversion Trader

> **Role**: Value-oriented counter-trend trader.
> **Style**: Buys dips (RSI oversold), sells rips (RSI overbought).
> **Optimization**: Gradient descent + nightly sweep.

---

## Entry conditions (all must pass)

1. `rsi_signal == "OVERSOLD"` OR `regime == "MEAN_REVERTING"` with strong composite
2. `conviction >= 0.25` — slightly higher bar for counter-trend trades
3. `position_count < max_positions`
4. `volume_pass == True`
5. NOT last trading day

## Exit conditions

- Stop-loss hit, take-profit hit, or RSI returns to neutral (50+)
- Forced EOD close on last day

## Position sizing

- `base_size_pct * 0.8` (slightly smaller — value plays take longer to pay off)
- Conviction multiplier applies above 0.5 conviction
- Max 5 concurrent positions

## Bankroll

- Start: $35 limit
- ±1%/trade based on 20-trade rolling win rate