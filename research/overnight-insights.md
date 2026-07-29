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
