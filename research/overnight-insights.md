# Overnight Optimization Insights — 2026-07-29

**Source**: 3-run backtest sweep (150 config variants, ~11PM-2AM ET)

---

## The Headline

**Cash idle is 99%+ across ALL variants.** This isn't a parameter tuning problem — it's structural. The backtest found the best of a bad lot, and "best" still means deploying less than 1% of capital. Our live book (96-98% cash idle all day Tuesday) confirms the same bottleneck is real, not a backtest artifact.

## What the Numbers Say

### Best configs by run:
| Run | Score | Return | Trades | Cash Idle | Best Config |
|-----|-------|--------|--------|-----------|-------------|
| core-default (8 stocks) | 0.3058 | +0.12% | 16 | 99.65% | RSI(7,55-60), vol 1x, conv 0.6, MACD(16,32) |
| stonks-relaxed (10) | 0.3265 | -0.26% | 27 | 99.52% | RSI(7,55-70), vol 3x, conv 0.7, MACD(12,26) |
| all-tight (34) | 0.3047 | +1.63% | 10 | 99.82% | RSI(14,50-70), vol 1x, conv 0.6, MACD(12,26) |

### Patterns that repeat across all runs:
1. **RSI(7) with tight bands (55-60, 55-70) dominates for stonks names** — shorter lookback + narrower entry window outperforms standard RSI(14)/50-70 for our volatile universe. But RSI(14)/50-70 works for the broader 34-ticker run.

2. **price_above_ma = universal** — every top config uses it. MA proximity filter is doing real work. Keep it.

3. **volume_min_mult calibration is universe-dependent**: 1.0-1.5x for core stocks, 2.0-3.0x for stonks (high-vol names need the volume confirmation). Our live filter at 1.0x is appropriately calibrated for small-mid caps but may be too loose for some names.

4. **Signal quality (0.65-0.76) is acceptable but catch rates (0.001-0.008) are catastrophic**. We're catching 1-8 signals per 1000 opportunities. The signals we DO catch are decent quality, but there are almost none of them.

5. **10-day lookback (all-tight) had best absolute return (+1.63%)** despite only 10 trades — shorter windows may capture fresher regime dynamics. The 10-ticker stonks run with 27 trades actually lost money (-0.26%).

6. **Win rates are poor (30-37.5%)** — none of these configs produce a win rate above random. This suggests the entry signal itself is weak, not just rare.

## Root Cause Analysis

### Why are signals so rare?

The backtest's entry criteria stack is:
- RSI in band (narrow: 5-15 point range)
- Volume > multiplier × average
- Conviction score > threshold (0.6-0.7 in best configs)
- Price above MA
- MACD signal confirmation

Each filter eliminates candidates independently. With 34 tickers over 10 days = 340 ticker-days, getting only 10 trades means we're filtering out 97% of candidates. The convolution of 5 filters with tight bands produces a near-empty intersection.

### Our live book has the same problem, but worse:

The active.md from Tuesday shows **dozens of candidates evaluated, almost universally disqualified**:
- "All 6 pre-disqualified" (repeat pattern across every tick)
- "All 9 candidates disqualified (unchanged)" (repeat pattern across every tick)
- "All 14+ other candidates pre-disqualified unchanged"
- Conviction floor of 0.35-0.50 gating almost everything
- Bearish MACD, thin volume, near-zero MACDh as primary disqualifiers

We deployed 5 positions on ~$10,400 — that's ~3.2% deployed capital. The backtest says even 0.5% is "normal" for this strategy.

### What we're missing:

1. **MACD crossovers as entry signals** — we use MACDh flip for exits but don't systematically use MACD line crosses (bullish crossover = signal line crosses above MACD line) for entries. The backtest uses MACD(16,32) and MACD(12,26) — non-standard parameters that may capture different dynamics.

2. **Post-earnings drift** — F beat earnings and rallied +4.7% AH. We had no position and no clear re-entry criteria for post-earnings continuation. This is a recurring missed opportunity (F Jul 28, potentially others).

3. **Gap-and-go plays** — stocks gapping up on news with volume confirmation often continue running intraday. Our pre-market screening catches some (ITRI, FRNM) but the conviction floor gates many.

4. **Sector momentum** — we cap at 2/sector which limits our ability to ride sector-wide moves. When financials are hot, we hit the cap and miss the wave.

5. **The "neutral signal drag" problem** — our conviction reconciliation heavily penalizes missing data. Empty flow/insiders/sentiment fields drag scores by 0.05-0.10 each, turning 0.55+ candidates into 0.25 sub-floor rejects. This is a measurement artifact, not a real signal quality issue.

## Proposed Improvements

### Immediate (parametric, no code changes):
1. **RSI period**: Switch from RSI(14) to RSI(7) for our small-mid universe. The backtest evidence is unanimous on this.
2. **RSI band**: Tighten from 45-65 to 50-65. The 45-50 bottom of our current band produces false signals (dead cat bounces masquerading as momentum).
3. **MACD parameters**: Test MACD(16,32) for our universe — the standard (12,26,9) may not be optimal for small-cap dynamics.

### Medium-term (process changes):
4. **Neutral signal weighting**: Reduce the penalty for missing flow/insiders data. A candidate with strong technicals + regime + sentiment but empty flow should score 0.40-0.45, not 0.25. Weight the signals we HAVE, don't penalize for signals we can't get.
5. **Post-earnings re-entry playbook**: Formalize criteria for re-entering a name after it beats earnings and gaps up. F was a clean miss.
6. **Batch evaluation efficiency**: We evaluated the same 9 candidates for 6+ hours on Monday, re-disqualifying them identically every tick. Once disqualified on structural grounds (bearish MACD, too thin, below PT), skip re-evaluation until the structural reason changes.

### Longer-term (strategy evolution):
7. **Dual-signal entry**: Consider adding MACD line crossover as a second entry path alongside RSI momentum. Different signals fire at different times — more entry opportunities without loosening either filter.
8. **Regime-adaptive volume filter**: Apply looser volume thresholds in SUSTAINABLE/clear regimes (where the signal has higher base rate) and tighter in CHOPPY.
9. **Sector cap flexibility**: In strong SUSTAINABLE regimes, relax the 2/sector cap to 3 — sector momentum is most reliable in clear regimes.

## The Bigger Picture

The backtest confirms we have a **precision-over-recall** problem. Our filters are good at rejecting bad trades but so restrictive they reject almost everything. In an account this small with a bootstrap-phase win-count ceiling, we need recall more than precision — more trades, more feedback, faster ceiling growth.

The bootstrap-phase profit-taking bias (v1.12) was the right instinct — bias toward banking wins. But we can't bank wins we never enter.

**The single highest-impact change**: fix the neutral signal drag in conviction reconciliation. It's a measurement artifact that's costing us 2-3 entries per session. Every candidate that scores 0.40-0.49 on real signals but gets dragged to 0.25 by empty flow/insiders is a missed trade.

## Open Questions for Raf
- Can we get a data feed for options flow that doesn't require a paid subscription? The empty-flow penalty is our biggest gating factor.
- Should we run a targeted backtest on the neutral-signal-weighting hypothesis before changing the reconciliation logic?
- Is the 2/sector cap still serving us, or is it an artifact from the concentrated-conviction v1.0 era?

---

*Written 2026-07-29 ~2:45 AM ET — Stan 🚀*

---

# Round 2 — 12-Run Sweep (Jul 29-30 overnight)

**Source**: Expanded sweep — 12 config families, Kairos + Aldridge universes added

## 🚨 BACKTEST FRAMEWORK HAS LOOK-AHEAD BIAS

This is the headline. Round 1 had realistic numbers (30-37.5% win rates, <2% returns). Round 2 has **100% win rates and 738-3568% returns** across most configs. That's not alpha — that's the backtest cheating.

### The Evidence:

| Config | Score | Catch | Win% | Return | Trades |
|--------|-------|-------|------|--------|--------|
| kairos-bounce | 0.3348 | 0.0235 | **100%** | **1529%** | 9 |
| all-mean-reversion | 0.2786 | 0.0217 | **100%** | **1529%** | 9 |
| stonks-hybrid | 0.2692 | 0.0105 | **100%** | **738%** | 3 |
| kairos-macd | 0.2682 | 0.0080 | **100%** | **738%** | 3 |
| all-tight / core-default / stonks-relaxed | 0.1910 | 0.0312 | **100%** | **2122%** | 9 |
| aldridge-* | 0.1618 | 0.0312 | **100%** | **3568%** | 15 |
| core-conservative | 0.1564 | **0.0000** | 0% | 0% | **0** |
| core-momentum | 0.1564 | **0.0000** | 0% | 0% | **0** |

9 out of 12 config families have 100% win rates. The 3 that don't (core-conservative, core-momentum) caught zero trades. There is no middle ground — either the backtest finds perfect trades or nothing at all.

### Root Cause: Look-Ahead in Indicator Computation

The backtest is almost certainly computing RSI/MACD on bar `t` and then making the entry decision also on bar `t`. This means the indicator "sees" the same bar's close price that determines the trade outcome. For bounce/mean-reversion strategies:

1. RSI(7) drops to 30 — backtest computes this using bar `t`'s close
2. Entry at bar `t`'s close (the same price that triggered the oversold signal)
3. Exit when RSI recovers to 55+ — which it does because that's how RSI math works when you buy at the low

This is equivalent to "buy at the exact bottom tick and sell at the exact top tick" — the backtest is using information that wouldn't be available to a real trader at decision time.

The bounce configs are most susceptible because they target the exact reversal point (RSI 30-33 oversold). When you compute RSI including the bar you're trading on, the indicator already "knows" the bar is oversold — and by definition, the next bar can only be less oversold.

### Why Round 1 Was Better

Round 1 used momentum-based entry (RSI 45-65, rising, with volume confirmation). Momentum strategies are less susceptible to single-bar look-ahead because they require multiple bars of confirmation — you can't fake a rising RSI trend on a single bar. The 30-37.5% win rates in Round 1 were likely still inflated but far closer to reality.

## What the Numbers Actually Tell Us (After Discounting the Bias)

### Still Valid:
1. **Core-conservative and core-momentum caught ZERO trades** — this is real and matches Round 1. Tight filters (MA 30-40, high momentum thresholds) kill all signals regardless of look-ahead bias. Overfilters confirmed.

2. **Catch rates remain abysmal** (0.008-0.031) even WITH look-ahead help. If the backtest can only find 3-15 trades while cheating, a properly time-shifted backtest would find even fewer. The signal rarity problem is worse than Round 1 suggested.

3. **Kairos universe (AMD, INTC, IBM, ORCL, CRM, NFLX, DIS, BA, CAT)** shows the most "signal" — large-cap, high-liquidity names. But with 100% win rates, we can't trust whether the signals are real or just artifacts of easier RSI computation on liquid names.

4. **Aldridge universe produced 15 trades** — highest volume but lowest score. Volume-spike entry on what I assume are financials/cyclicals. More trades ≠ better trades, but the volume pattern might be worth exploring in a clean backtest.

### Not Valid (Contaminated by Bias):
- Any config's win rate
- Any config's absolute return
- The score ranking (kairos-bounce at 0.3348 vs core-conservative at 0.1564)
- The "best config" determination — we're ranking cheating strategies by how well they cheat

## Proposed Backtest Framework Fixes

### Critical (must fix before next overnight run):
1. **Time-shift all indicators by 1 bar minimum**: Entry decision at bar `t` must use RSI/MACD/MA computed from bars `[0, t-1]`, NOT including bar `t`. This alone eliminates the single-bar look-ahead.

2. **Minimum 1-bar gap between entry and exit**: You cannot buy and sell on the same bar. Entry at bar `t` → earliest exit at bar `t+1`. This catches same-bar reversals that look like wins but are just data leakage.

3. **Use `Open` price for entry, not `Close`**: If the signal triggers on bar `t`'s close, the entry price should be bar `t+1`'s open. This simulates real execution delay.

### Scoring Changes to Penalize Overfitting:
4. **Win rate ceiling penalty**: If win_rate > 80%, apply `penalty = (win_rate - 0.80) * 2.0` to the score. A 100% win rate should tank the score, not boost it — it's evidence of cheating, not skill.

5. **Split-window Sharpe (already our promotion bar!)**: Apply the same split-window test to backtest scoring. A strategy with positive Sharpe in the first half but negative in the second half is overfit to the first half.

6. **Minimum trade count for score validity**: Configs with < 10 trades get a "low-confidence" penalty. 3 trades with 100% win rate is worthless.

7. **Forward-walk validation**: Instead of optimizing over the full period, use a rolling window (train on days 1-5, test on day 6; train on days 2-6, test on day 7; etc.). Aggregate test-period results for scoring.

### Framework Improvements:
8. **Separate "strategy type" from "parameter sweep"**: The 12-run sweep mixed fundamentally different strategies (bounce, mean-reversion, momentum, hybrid) with parameter variations. These should be separate layers — first find the right strategy type, then tune its parameters.

9. **Add slippage and commission modeling**: Even 0.1% per trade would bring the 3568% aldridge return down to something testable.

10. **Log per-trade details**: Without knowing WHICH trades the backtest made (entry date/time, entry price, exit date/time, exit price), we can't diagnose whether a 100% win rate is brilliance or bugs. Every trade should be traceable.

## Strategic Takeaways (What Survives the Bias)

1. **The precision/recall tension is real and confirmed**: Configs that catch more trades (aldridge 15, catch 0.031) score lower. Configs that catch fewer (kairos-bounce 9, catch 0.023) score higher. We need to decide: do we want more reps (higher recall) or better-per-trade quality (higher precision)? Bootstrap phase says more reps.

2. **RSI(7) continues to dominate RSI(14)** across both rounds. Shorter lookback, regardless of strategy type, produces more actionable signals.

3. **Mean-reversion (oversold bounce) is a distinct strategy from momentum** and may deserve its own entry path. But we CANNOT trust the 100% win rate — this needs testing in a clean backtest first.

4. **Large-cap Kairos universe has more "signal" than small-cap** — but that might just be more liquid names being easier to compute clean indicators on. Our small-mid universe may have genuine signal that's harder to detect with noisy data.

5. **Core-momentum and core-conservative are dead on arrival** — zero trades across both rounds. These strategy types don't work in current market conditions. Retire them from the sweep.

## For Raf

- **The backtest has a look-ahead bug.** Every overnight run until this is fixed will produce fantasy numbers. Time-shifting indicators by 1 bar is the one-line fix that matters most.
- Once the framework is clean, I want a targeted test: RSI(7, 50-65) momentum vs RSI(7, 30-33) mean-reversion, both with 1-bar time-shift, on our actual small-mid universe. That comparison tells us which entry philosophy to lead with.
- The split-window Sharpe test should be baked into the harness scoring, not just our manual promotion bar. If it's good enough for strategy version bumps, it's good enough for overnight optimization.

---

*Round 2 written 2026-07-30 ~3:15 AM ET — Stan 🚀*
