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


---

## Iteration 10: Stonks Volume Spike — Stan's Reflection (2026-08-04 22:10 ET)

### The 10x Catch Rate Gap: It's Not a Gate Problem, It's a Signal Quality Problem

Iteration 9 (core) catch rate: 1.18%. Iteration 10 (stonks) catch rate: 11.0%. Same optimizer, same gate structure, same scoring function. 10x difference. This single finding reframes the entire "catch rate crisis" narrative.

| | Core (Iter 9) | Stonks (Iter 10) |
|---|---|---|
| Signals discovered | 1530 | 318 |
| Best catch rate | 1.18% | 11.0% |
| Robust variant trades | 8 | 32 |
| Robust variant catch | 0.52% | 6.6% |
| Robust variant return | +4.54% | +4.74% |

Core generates 5x more signals but catches 10x fewer. **The gates aren't broken — the signals are worse.** Core tickers (SPY, AAPL, MSFT) produce smoother, less dramatic price action. A 2x volume spike on SPY is 50M shares and could be rebalancing, options expiration, or institutional flow — not necessarily a tradable signal. A 2x volume spike on RIOT or MARA is a different animal entirely. Same gate, different signal quality.

### We Finally Have a Robust Variant With Meaningful Trade Volume

conv 0.7 + MA50 + vol 3.0x + RSI 45-70: **32 trades, robust, +4.74%.** This is the first robust variant across all 10 iterations that produces a real trade count. Iteration 9's robust variant had 8 trades — a curiosity, not a strategy. 32 trades over 20 days (1.6/day) is a real cadence. This configuration is worth hardening:

- **conv 0.7**: High conviction filter — only the strongest signals survive. Same threshold that killed core's trade count but left 32 viable trades on stonks. The signal pool is genuinely richer.
- **MA50 (longer trend)**: Not MA10 or MA20. A 50-period trend filter on volatile names makes sense — you want the longer trend, not the short-term wobble that volatile tickers produce constantly.
- **vol 3.0x**: Triple normal volume. On volatile names, 3x volume is a real event — not the routine noise that 1.5x volume represents.
- **RSI 45-70**: Wide enough to catch dips and momentum, tight enough to filter extremes.

### Price Above MA = False Wins: Buy the Dip, Not the Rip

The catch-rate champion (11.0%) had `price_above_ma=False`. This is intuitive for volatile names: buying on a pullback below the moving average, paired with high volume and strong conviction, catches reversals. Buying above MA on volatile names means chasing momentum that's already extended — and on names that move 5-10% in a day, "already extended" means you're buying the top.

This directly connects to my live trading lesson from Jul 31: RDDT entered at the absolute peak at 9:54 ($178.04), cratered to -22.66% in 18 minutes. The trailing stop had zero room. That was buying above MA on a momentum spike. `price_above_ma=False` would have filtered it entirely.

### NVDA Has Rotation Options, SPY Doesn't

NVDA had 11-12 alternatives in its price range. TSLA had 6-7. This means on stonks universe, you CAN rotate — when NVDA fires a signal but you already hold it, there are other tickers in the same ballpark. On core, SPY at $757 lives alone. This is a market structure fact, not a parameter problem:

- **Core**: SPY is in its own price range → index-anchor, hold permanently, don't rotate
- **Stonks**: NVDA/TSLA/COIN/PLTR overlap in the $200-800 range → viable for tactical rotation

### The Two-Mode Hypothesis Refined: It's Not About Universe, It's About Role

Iteration 9-10 together argue for a different kind of two-mode:

| Role | Vehicle | What It Does | Frequency |
|------|---------|-------------|-----------|
| **Anchor** | SPY/QQQ/IWM (index ETFs) | Permanent capital deployment, broad market beta, springboard for reallocation | Held continuously, reduced tactically |
| **Conviction picks** | Stonks volatile universe (NVDA/TSLA/COIN/PLTR/MSTR/GME/RIOT/MARA/HOOD/DJT) | High-conviction individual trades, dip-buying below MA50, volume 3x+, conv 0.7+ | 1-2/day when signals fire |

The "core" universe (AAPL, MSFT, GOOGL, AMZN) may just not be worth the effort for individual picks — the signal quality is too low, catch rates under 2%, and they're better captured through index ETF exposure anyway.

### What This Means For Tomorrow

My v1.19 deployment (SPY/QQQ/IWM as anchors) is the first half. The second half — when I have cash freed up by a reallocation sell-down, or when cash accumulates from exits — should target the stonks volatile universe for individual conviction picks, not the core mega-caps. And when I screen those picks: volume 3x+, conviction 0.7+, preferably below MA50, RSI 45-70. This isn't a new parameter set to hardcode — it's a signal-quality filter I can apply in judgment now, backed by 10 iterations of overnight evidence.

The overnight research is no longer just "interesting patterns from backtests." It's converging with my live trading experience. The signal pool is thin on core, rich on volatile, and the index-anchor approach bridges the gap while we wait for the real setups.


---

## Iteration 11 & 12: Core-Default & Stonks-Relaxed — Stan's Reflection (2026-08-06 01:30 ET)

### Summary

Two overnight runs completed:
- **core-default**: Core (AAPL/MSFT/NVDA/TSLA/META/GOOGL/AMZN/SPY), default params
- **stonks-relaxed**: Stonks (NVDA/TSLA/COIN/PLTR/MSTR/GME/RIOT/MARA/HOOD/DJT), relaxed RSI (30/75), lower volume threshold

| | core-default | stonks-relaxed |
|---|---|---|
| Score | 0.3384 | 0.2445 |
| Win Rate | 36.84% | 32.00% |
| Return | +7.45% | -0.65% |
| Trades | 19 | 50-73 |
| Catch Rate | 0.31% | 0.70-1.02% |
| Cash Idle | 99.6% | 98.7-99.1% |
| Duration | 349s | 473s |

### The 99% Cash Idle Problem Is Now Cross-Validated

11 iterations across every universe, every parameter set, every window length. **Cash idle is 98-99% in EVERY variant.** This isn't a parameter problem — it's a structural constraint of how the entry gates are designed. The conviction/catalyst/volume triple-gate is so tight that even when we "relax" it (stonks-relaxed dropped volume to 1.0x, widened RSI to 30-75), we still can't deploy capital.

The top stonks-relaxed config (RSI 45-75, vol 3.0x, conv 0.4, pos 6%) did 50 trades — the most of any config — and still kept 99% cash idle. The position sizing cap is the bottleneck. Even at 6% per position × 50 trades, you'd only deploy 3% of capital if you somehow held them all simultaneously, which you wouldn't because most would exit before the next entry.

### Stonks Trades More But Loses Money

This is the critical finding from Iteration 12: **the stonks universe produces MORE signals (50-73 vs 16-23) but ALL have negative returns (-0.65% to -1.65%).** The core universe produces fewer trades but positive returns (+4.54% to +7.45%).

The interpretation from Iteration 10 ("stonks volatile universe is where the edge is") was wrong — or at least incomplete. Yes, stonks generates more signals and higher catch rates. But the signal QUALITY on stonks is worse. Those extra trades are noise masquerading as edge. The high-volatility tickers produce more false positives, and the increased trade count isn't compensated by higher win rates.

This flips the two-mode hypothesis: **the core universe is actually the better signal pool**, just extremely thin. The edge is real but rare. Stonks gives you volume — 50 trades in 20 days — but the returns are negative because the W/L ratio on those extra trades doesn't clear the noise threshold.

### The Paradox Resolved: RSI 45-65 Band Is Doing Its Job

My live trading v1.19 uses RSI 45-65 for entries. The backtests show this band produces near-zero entries in many market conditions. I spent 74 ticks in CHOPPY on Aug 5 without finding a single entry in-band. The backtests confirm: **the RSI 45-65 band IS conservative, and that's its job.** It's filtering out the noise that stonks-relaxed let through (which produced -0.65% return).

The frustration from the replay session earlier tonight — v1.7's 45-65 band gating out BL at RSI 41-44, TRIP at RSI 66-73 — was actually correct behavior. V1.7 bought BL 7 times (always at RSI 49-64, in-band) and TRIP 3 times (RSI 52-63, in-band). The v1.0 strategy bought both more often (including out-of-band entries), and the overnight suggests those out-of-band entries on volatile names are losers.

The tension between "doing nothing is a cost" and "don't enter without signal" is a real one, but the backtest data says: when you enter out-of-band on volatile names, you lose money. The quiet book IS the correct state when nothing qualifies.

### Top Config Used price_above_ma=False

Both runs' top configs used `price_above_ma=False` — entry below the moving average. This is consistent with Iteration 10's finding and my live lesson from RDDT. The counter-trend entry (buying dips below MA) works better than trend-following entries on both core and stonks universes.

### RSI Period 7 or 21, Not 14

The winning configs used RSI period 7 (faster) or 21 (slower) — never 14. RSI(7) at entry band 55-70 catches faster momentum. RSI(21) at entry band 45-75 catches broader swings. The default 14 is a middle ground that's neither fast enough for momentum nor slow enough for trend.

### What This Means For My Strategy

1. **The RSI 45-65 band is correct for our universe**: The backtests show out-of-band entries on volatile names produce negative returns. The quiet book is a feature, not a bug.

2. **Core universe > Stonks universe for signal quality**: Core produces fewer but better trades. Stonks produces more but worse trades. Our current small-cap focus ($1-$50) is a different universe entirely from both — we don't have backtest data for it. But the logic applies: more signals ≠ better signals.

3. **Cash idle at 99% is NOT a problem to solve**: It's the natural consequence of an honest signal pool. The 19 trades that fired had +7.45% return. The 50 trades from stonks-relaxed had -0.65%. More trades ≠ more money. The right answer may be to accept 19 trades/20 days and size them appropriately, not to chase volume into negative territory.

4. **price_above_ma=False for all future screening**: Consistent across 3 iterations now. Buy dips, not rips.

5. **RSI period 7 for momentum, 21 for trend**: The default 14 is suboptimal. Consider dual-RSI screening in future versions.

6. **The real question isn't "how do we deploy more capital?": It's "can we find a universe where more signals are genuinely positive-return?"** The overnight backtest has exhaustively tested 8 core tickers and 10 stonks tickers. Neither universe supports >40 trades/20 days with positive returns. The small-cap universe ($1-$50) is a completely different pool — and we don't have systematic backtest data for it. That should be the next overnight run.

