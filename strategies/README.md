# Strategies

> Strategy configuration for the paper trading system.
> Each trader reads these files to inform its decisions.

---

## Files

| File | Purpose |
|------|---------|
| `active.md` | Human-readable strategy description with regime weights, thresholds, and optimization loop |
| `params.json` | Machine-readable parameter values (SignalParams). Traders read this for current config |

---

## How it works

1. **Trader reads `params.json`** at startup to get current signal thresholds, position sizing, risk limits
2. **Trader calls `SignalEngine.process(tick)`** each tick — the engine uses these params to compute RSI, momentum, volatility, regime
3. **Trader decides** based on `SignalReport`: entry conditions, exit conditions, position sizing
4. **Nightly sweep** tests 8+ variant parameter sets, scores each via replay on yesterday's data
5. **Best variant** updates `params.json` for the next day
6. **Gradient descent** tweaks parameters intraday by replaying recent ticks

---

## Evolution loop

```
params.json → trader reads → trades happen → data logged
    ↑                                            ↓
    |                                     nightly replay scores
    |                                            ↓
    └──── best variant updates params ←──── leaderboard picks winner
```

Each night the system runs 8 variants (wider_stops, tighter_stops, aggressive_sizing, conservative_sizing, momentum_focus, mean_reversion_focus, trend_following, volatility_adaptive). The winner replaces `params.json` if it beats baseline.

---

## Bankroll

Traders also read bankroll state from their workspace (`bankroll.md`). Starting limit: $35. Adjusts ±1%/trade based on 20-trade rolling win rate.