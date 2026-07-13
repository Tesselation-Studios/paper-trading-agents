# Active Strategy — Kairos (Momentum-Driven)

> **Source**: `paper-trading-rebuild/src/signals.py` (SignalParams)
> **Optimization**: Gradient descent (intraday) + Nightly sweep (overnight)
> **Last updated**: 2026-07-13

---

## 1. Core Logic

The trader reads a **SignalReport** (from `SignalEngine.process(tick)`) each tick and decides:

| Signal | What it means | Entry condition |
|--------|---------------|-----------------|
| `momentum_signal == "BULLISH"` | Price momentum above threshold | Enter long |
| `rsi_signal == "OVERSOLD"` | RSI below `rsi_oversold` | Potential reversal entry |
| `composite_signal > 0.3` | Weighted blend of all signals | Strong entry signal |
| `conviction >= 0.20` | Engine confidence in signal | Entry gate |

**Exit conditions**: Stop-loss hit, take-profit hit, or forced EOD close.

---

## 2. Current Parameters

See `params.json` for exact values. Key thresholds:

| Parameter | Current | Range | Effect |
|-----------|---------|-------|--------|
| `momentum_threshold` | 0.30–0.55 | [0.3, 0.9] | Lower = more trades |
| `momentum_lookback` | 14–20 | [5, 60] | Fewer bars = faster signals |
| `rsi_oversold` | 30–35 | [15, 40] | Higher = more oversold triggers |
| `rsi_overbought` | 65–70 | [60, 85] | Lower = more overbought triggers |
| `base_size_pct` | 12–15% | [5%, 30%] | % of equity per position |
| `max_positions` | 5–10 | [1, 10] | Max concurrent positions |
| `stop_loss_pct` | 1–5% | [2%, 10%] | Per-position stop |
| `take_profit_pct` | 3–15% | [5%, 30%] | Per-position take profit |

---

## 3. Regime Weights

The engine classifies the current market regime and adjusts position sizing:

| Regime | Weight | Behavior |
|--------|--------|----------|
| `TRENDING_UP` | 1.0–1.2 | Full size, momentum following |
| `TRENDING_DOWN` | 0.5 | Reduced size, short bias (paper) |
| `MEAN_REVERTING` | 0.8–1.0 | Contrarian entries, RSI-driven |
| `HIGH_VOLATILITY` | 0.4 | Reduced size, tighter stops |

---

## 4. Optimization Loop

```
Every tick (intraday):
  gradient_descent(params, replay(last_N_ticks))
  → perturb one parameter → replay → score → step toward better score

Every night (overnight):
  nightly_sweep(yesterday_data)
  → 8+ variants → score each → pick best → update params.json
```

The best-scoring variant from the nightly sweep becomes the next day's baseline. Scores are logged to Postgres (`param_history` table) for trend analysis.

---

## 5. Variant Templates

Eight strategy templates tested every night:

| Template | What it does |
|----------|-------------|
| `wider_stops` | +50% SL/TP, more room to breathe |
| `tighter_stops` | -30% SL/TP, quick exits |
| `aggressive_sizing` | +40% position size, more conviction |
| `conservative_sizing` | -40% position size, capital preservation |
| `momentum_focus` | Heavy momentum weight, lighter mean-reversion |
| `mean_reversion_focus` | Heavy RSI/mean-reversion, lighter momentum |
| `trend_following` | Long lookback, strong trend bias |
| `volatility_adaptive` | Tighten in high vol, loosen in low vol |

---

## 6. Bankroll

- **Starting limit**: $35
- **Adjustment**: ±1% per trade based on 20-trade rolling win rate
- **All trades count**: live + sim
- **Max drawdown**: 15% = paused, 20% = eliminated