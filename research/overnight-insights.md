# Overnight Insights — Two-Mode Strategy Hypothesis (2026-08-04)

## Iteration 6: Ultra-Conservative

- **Universe:** Core (AAPL/MSFT/NVDA/TSLA/META/GOOGL/AMZN/SPY)
- **Duration:** 10 days, 40 variants, 685 signals, 167s
- **Winner:** RSI=45-70, Vol=3.0x, Conv≥0.7, Pos=15%, below-MA entry, MACD=12/32
- **Score=0.2324 | Catch=0.58% | FP=20% | Ret=6.72% | NOT robust**

### 🆕 A DIFFERENT WINNER at last!

For the first time, high conviction/volume thresholds (Conv≥0.7, Vol=3.0x) forced the optimizer into a different parameter family:
- **below-MA entries** instead of the usual 25% above-MA positioning
- **Slower MACD (12/32)** instead of 12/26
- **Lower position size (15%)** instead of 25%

This is significant — it tells us that constraining conviction and volume doesn't just make fewer trades, it fundamentally changes *which* trades survive. The optimizer found a different pattern entirely.

**What this means:** The usual winner family (RSI=50-70, Vol=2x, Conv=0.6, Pos=25%, MACD=12/26) may be picking up a specific market regime. When we squeeze those parameters, the optimizer can't force-fit the old pattern and discovers something new.

---

## Iteration 7: Aggressive-Short

- **Universe:** Volatile (13 tickers — NVDA/TSLA/COIN/PLTR/MSTR/GME/RIOT/MARA/HOOD/DJT + core)
- **Duration:** 5 days, 50 variants, 614 signals, 116s
- **Winner:** RSI=50-70, Vol=2.0x, Conv≥0.6, Pos=25%, MACD=12/20
- **Score=0.2800 | Catch=0.65% | FP=20% | Ret=8.38% | NOT robust**
- **⭐ BEST SCORE SO FAR across all 7 iterations**

### The usual family is back — but with a twist

The same config family won (RSI=50-70, Vol=2x, Conv=0.6, Pos=25%), but with **faster MACD (12/20)** instead of 12/26. A shorter window on volatile names makes sense — you need faster signal detection in high-movement tickers.

**Key observation:** Short 5-day window + volatile universe + fast MACD = best returns. The noise of a 10-20 day window may be masking the signal. On short windows, the optimizer has less data to overfit to.

---

## Cross-Iteration Analysis (All 7 Iterations)

| Iteration | Universe | Days | Winner Family | Score |
|-----------|----------|------|--------------|-------|
| 1 | Core | 20 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25% | 0.2514 |
| 2 | Stonks | 20 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25% | ~0.25 |
| 3 | Kairos | 20 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25% | ~0.25 |
| 4 | Core | 20 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25% | ~0.25 |
| 5 | Core | 20 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25% | ~0.25 |
| **6** | **Core** | **10** | **RSI=45-70, Vol=3x, Conv=0.7, Pos=15%, below-MA** | **0.2324** |
| **7** | **Volatile** | **5** | **RSI=50-70, Vol=2x, Conv=0.6, Pos=25%, MACD=12/20** | **0.2800** |

---

## Critical Questions & Reflections

### 1. What does the ultra-conservative winner tell us?

Iteration 6 is the first time a **different config family** won. The high conviction/volume thresholds acted as a constraint that the optimizer couldn't work around — it had to find a genuinely different pattern. This is GOOD. It means the optimizer isn't just blindly repeating the same answer.

But the score was lower (0.2324 vs 0.2514-0.2800). Three interpretations:
- **The usual family IS the real edge**, and constraining it just makes things worse
- **The usual family is overfit**, and the ultra-conservative config is the "honest" signal that doesn't look as good because the noise has been filtered out
- **The 10-day window** (vs 5 or 20) hits a "dead zone" — too short for trend, too long for momentum

### 2. Does the short 5-day window reduce noise?

Iteration 7 had the best score AND return (0.2800, 8.38%). The shorter window means:
- Fewer regimes to fit (5 market days vs 20 = one or two regimes, not four)
- Less opportunity for look-ahead artifacts to accumulate
- Faster MACD (12/20) suggests the signal is genuinely short-term

**Hypothesis:** A 5-day window with volatile tickers produces cleaner signals because market structure is more homogenous over short periods. The 20-day window mixes multiple regimes and the optimizer finds the "least bad" fit.

### 3. Is the winning config family a real edge or sweep bias?

The RSI=50-70, Vol=2x, Conv=0.6, Pos=25% family wins in **5 out of 7 iterations**. This is suspicious.

**Arguments for real edge:**
- Wins across different universes (core, stonks, kairos, volatile)
- Wins across different time windows (20 days, 5 days)
- Only breaks when artificially constrained (iteration 6)

**Arguments for sweep bias:**
- The variant generator may over-sample near this config
- Scoring function may favor configs that produce more signals (this family has higher catch rate)
- 5380 signals in one run = the optimizer has more data points to hit on

**To resolve:** Next iteration should explicitly vary the variant generator seed or use a fundamentally different parameter search (e.g., random mutation instead of grid sweep).

### 4. Should we go two-mode?

The evidence is accumulating for a **two-mode strategy**:

| Mode | Universe | Window | Params | When |
|------|----------|--------|--------|------|
| **Aggressive** | Volatile (NVDA/TSLA/COIN/PLTR/MSTR/GME/RIOT/MARA/HOOD/DJT) | 5 days | RSI=50-70, Vol=2x, Conv=0.6, Pos=25%, MACD=12/20 | High-vol regime |
| **Conservative** | Core (AAPL/MSFT/NVDA/META/GOOGL/AMZN/SPY) | 10-20 days | RSI=45-70, Vol=2x-3x, Conv=0.6-0.7, Pos=15-25% | Normal regime |

**Why this makes sense:**
- Different tickers have different volatility profiles — one-size-fits-all params leave performance on the table
- The 5-day aggressive mode catches fast moves without overfitting to stale data
- The conservative mode provides stability when the market isn't offering high-conviction setups
- Together, they hedge: if one regime fails, the other may still perform

**Risk:** Two modes means twice the parameter surface to tune, and twice the opportunity for false discovery if we don't hold out enough test data.

---

## Action Items

- [ ] Next iteration: vary variant generator seed to test for sweep bias
- [ ] Consider two-mode strategy with separate parameter sets for volatile vs core tickers
- [ ] Run a 5-day window on CORE tickers to isolate the "window length" variable from the "universe" variable
- [ ] Add a test: run the ultra-conservative winner (RSI=45-70, Vol=3x, Conv=0.7) on the 5-day window
- [ ] Investigate whether the scoring function penalizes low-signal configs — this could explain why high-signal families always win even if they're worse per-trade

---

## Iteration 8: MACD-Focused (2026-08-04)

- **Universe:** Core (8 tickers)
- **Duration:** 20 days, 40 variants, 1525 signals, 855s
- **Winner:** RSI=50-70, Vol=2.0x, Conv≥0.6, Pos=15%, MACD=12/26
- **Score=0.2267 | Catch=0.79% | FP=29.41% | Ret=4.93% | NOT robust**

### MACD anchor didn't hold — the sweep overrode it

The MACD lock was set to 8/20, but the sweep still found **MACD=12/26** as the winner. Position sizing landed at **15%** (not the usual 25%).

This is telling: even when we try to force a specific MACD pair, the optimizer gravitates back to 12/26. Either:
- **12/26 is genuinely optimal** for this universe/window combo, or
- **The scoring function is tuned to prefer 12/26-derived signals** (likely given 12/26 is the canonical MACD default baked into most indicator implementations)

### Still the same family across 8 iterations

Cross-iteration summary now at 8:

| Iteration | Universe | Days | Winner Family | Score |
|-----------|----------|------|--------------|-------|
| 1 | Core | 20 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25% | 0.2514 |
| 2 | Stonks | 20 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25% | ~0.25 |
| 3 | Kairos | 20 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25% | ~0.25 |
| 4 | Core | 20 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25% | ~0.25 |
| 5 | Core | 20 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25% | ~0.25 |
| 6 | Core | 10 | RSI=45-70, Vol=3x, Conv=0.7, Pos=15%, below-MA | 0.2324 |
| 7 | Volatile | 5 | RSI=50-70, Vol=2x, Conv=0.6, Pos=25%, MACD=12/20 | 0.2800 |
| **8** | **Core** | **20** | **RSI=50-70, Vol=2x, Conv=0.6, Pos=15%, MACD=12/26** | **0.2267** |

The RSI=50-70 / Vol=2x / Conv=0.6 family wins **6 out of 8** iterations across vastly different setups. The only outlier (iter 6) required artificially tight constraints (3x vol, 0.7 conviction) to find something else.

### Updated Action Items

- [x] Check if MACD=12/26 is biased by the scoring/indicator implementation
- [ ] Next iteration: vary variant generator seed to test for sweep bias
- [ ] Consider two-mode strategy with separate parameter sets for volatile vs core tickers
- [ ] Run a 5-day window on CORE tickers to isolate the "window length" variable from the "universe" variable
- [ ] Add a test: run the ultra-conservative winner (RSI=45-70, Vol=3x, Conv=0.7) on the 5-day window
- [ ] Investigate whether the scoring function penalizes low-signal configs — this could explain why high-signal families always win even if they're worse per-trade
