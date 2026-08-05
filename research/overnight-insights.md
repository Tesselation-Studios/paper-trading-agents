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

---

## Iteration 9: Wide RSI Net — Stan's Reflection (2026-08-04 22:05 ET)

### The Catch Rate Problem Is Not What It Looks Like

18 out of 1530 signals caught. 1.18%. That's not a discovery overcount — it's a **signal-quality crisis masquerading as a catch-rate problem**. Here's why:

The wide RSI net (40-75) was supposed to cast a bigger net. It didn't. Catch went DOWN, not up, relative to narrower ranges. The bottleneck isn't the RSI gate — if RSI 40-75 catches 1.18%, and RSI 50-70 catches ~0.8%, the difference is marginal. The bottleneck is **everything after the technical screen**: conviction scoring, catalyst presence, volume thresholds, and whatever scoring function is deciding what makes a signal "actionable."

1530 signals discovered, 18 caught. The 1512 that didn't make it aren't getting caught by slightly wider RSI nets. They're failing deeper checks — the ones that actually separate noise from edge.

### The Robustness Paradox: Only the Filter That Never Fires Is Robust

This is the most important finding, and it's been hiding in plain sight across all 9 iterations:

| Config | Robust? | Trade Count |
|--------|---------|-------------|
| Conv 0.5, RSI 50-60, Vol 1.5x | ❌ | 17 |
| CEO 0.1, Conv 0.6, RSI 50-65, Vol 3.0x | ❌ | 14 |
| **Catalyst 0.3, Conv 0.7, RSI 55-60, Vol 3.0x** | ✅ | **8** |

The ONLY robust variant is the one that barely trades. Every looser config produces more trades with worse robustness. The relationship is monotonic: **more trades = less robustness, less trades = more robustness.**

This isn't a parameter tuning problem. This is a **signal-to-noise problem in the underlying data.** The signal pool has a tiny nucleus of genuine edge (8 trades in 20 days) surrounded by a massive cloud of noise (1500+ trades). Looser filters don't capture more edge — they capture noise that happens to look like edge in the training window.

**Implication**: We can't solve the catch-rate problem by relaxing gates. Relaxing gates just lets noise through. We need to either:
1. Find a different signal source entirely (not just re-tuning the same RSI/MACD/conviction combo)
2. Accept that 0.4 trades/day is the honest rate in this universe and size them bigger
3. Use the broad market as the primary vehicle (index anchors) and treat individual-stock picks as rare, high-conviction supplements

### The Winning Family Is a Mirage — And We Have 9 Iterations of Evidence

RSI=50-70, Vol=2x, Conv=0.6, Pos=25% won 6 out of 9 iterations. It has NEVER been robust. Not once. The only time a different config won was when we artificially constrained the optimizer so hard it couldn't find the usual family (iteration 6).

This is the classic overfitting signature: a config that looks great on aggregate returns but can't survive a split-window test. The optimizer isn't finding edge — it's finding the config that happened to work in this specific 20-day window. When you split the window, the pattern doesn't hold.

**The scoring function is part of the problem.** If the scoring function penalizes low-signal configs, then configs that fire more often (the 17-trade family) will always outscore configs that fire rarely (the 8-trade robust family), even if the rare config is genuinely better per-trade. The optimizer is being steered toward high-trade-count configs regardless of quality.

### SPY Zero-Alternatives: The Finding That Validates v1.19

SPY trading days had zero alternative tickers in the same price range across all 20 days. This isn't a bug — it's a market structure fact. SPY at $750+ lives in a price range where no other liquid ticker trades. You can't build a "rotation out of SPY" strategy because there's nothing to rotate INTO.

This directly validates today's v1.19 index-anchor framework. If SPY has no peers, don't try to time it. Hold it as a permanent anchor, reduce tactically when individual-stock opportunities clear their own gates, and let the broad market do the heavy lifting while we wait for the rare individual-stock conviction setup.

### What This Means For My Trading Tomorrow

The overnight backtest and my live trading are saying the same thing: **individual-stock signals are extremely thin right now.** I spent 74 ticks today unable to find a single qualifying entry. The backtest spent 20 days finding 8 robust trades. Same signal, different lens.

The v1.19 index-anchor springboard isn't a distraction from the "real" strategy — it IS the strategy while the individual-stock signal pool is this thin. Deploy into SPY/QQQ/IWM, let the broad market compound, and when a genuine individual-stock signal appears (the kind that would survive a robustness check), fund it by selling down an anchor.

### Proposed Improvements

1. **Scoring function redesign**: The current scorer penalizes low-trade-count configs, which means it will never select the genuinely robust variant. The split-window Sharpe test should be the primary scorer, not a secondary check. A config with 8 robust trades and a positive split-window Sharpe should outscore one with 17 non-robust trades and a higher aggregate return.

2. **Two-mode isn't the answer — index-anchor + rare conviction picks is**: The two-mode hypothesis (volatile universe + core universe with different params) makes intuitive sense but adds complexity without addressing the fundamental problem: noise dominates signal across BOTH universes. The robust variant found 8 trades in 20 days on core. On volatile, it might find 12. Still not a strategy. The index-anchor model is simpler and directly addresses the deployment problem.

3. **Test the index-anchor thesis itself**: Next overnight run: simulate holding SPY/QQQ/IWM as permanent positions, selling down 10-20% when individual-stock signals fire, re-buying when cash accumulates. Compare total return against the current "wait for individual signals only" baseline. I suspect the index-anchor baseline will dominate purely on deployment efficiency — 92% cash earns 0%, SPY earns whatever SPY earns.

4. **The signal pool needs new sources**: RSI/MACD/conviction/volume have been exhaustively swept across 9 iterations and the ceiling is 8 robust trades in 20 days. The next signal source should come from outside this family — cross-sectional momentum rank, congressional trade following, or a sector-rotation signal. Something that isn't just another way of re-slicing the same technical indicators.

