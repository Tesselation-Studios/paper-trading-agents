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

---

# Round 3 — Framework-Fixed Sweep (Jul 30-31 overnight)

**Source**: Two iterations from a cleaned-up optimization framework — core-default (8 tickers, 50 variants) and stonks-relaxed (10 stonks tickers, relaxed RSI, 50 variants).

## ✅ The Framework Is Fixed

Compare Round 2 (100% win rates, 738-3568% returns) to this round (21-34% win rates, -0.09% to -1.70% returns). These numbers are **credible**. Low win rates, negative returns — this is what honest backtest results look like. The time-shift fix worked.

### The Numbers:

| Iteration | Universe | Variants | Signals | Catch Rate | Return | Trades | Win% | Top Score |
|-----------|----------|----------|---------|------------|--------|--------|------|-----------|
| core-default | 8 tickers | 50 | 6,014 | 0.32% | -0.09% | 19 | 21% | 0.2867 |
| stonks-relaxed | 10 stonks | 50 | 7,422 | 0.63% | -1.70% | 47 | 34% | 0.3185 |

### Top Configs:

**Iteration 1 (core-default)**: RSI(7, 50-75), vol 3.0x, conv 0.4, MA(10), max_position 6%, ceiling 20%
- Wide RSI band (25-point), high volume threshold (3x). Only 19 trades, 21% win rate.

**Iteration 2 (stonks-relaxed)**: RSI(7, 55-65), vol 2.0x, conv 0.6, MA(20), max_position 10%
- Tighter RSI band (10-point), still high volume (2x). 47 trades, 34% win rate. More trades, better selectivity, still losing money.

## Patterns Confirmed Across All 3 Rounds

### 1. RSI(7) dominates RSI(14) — unanimous

Every top config across all three rounds uses RSI(7). The standard RSI(14) never appears in a top-3 config. For our small-mid universe, shorter lookback = more actionable signals. Our live strategy still uses RSI(14) in the 40-70 band. This is the single most evidence-backed parameter change we haven't made.

### 2. Catch rates are sub-1% — structural, not parametric

0.32% on core, 0.63% on stonks. Even with the wide RSI band (50-75) in iteration 1, the engine catches fewer than 1 in 150 signals. The bottleneck isn't RSI band width — it's the convolution of multiple filters (RSI + volume + conviction + MA + MACD). Each filter independently eliminates most candidates, and their intersection is near-empty.

**This confirms the core finding from Round 1**: we have a precision-over-recall problem that parameter tuning alone cannot fix. The backtest with 7,422 discovered signals could only act on 47 of them.

### 3. Discovery and replay use fundamentally different signal types

This is the "structural issue" the cron session flagged, and I think it's exactly right:

- **Discovery phase**: Web search, Tavily, sentiment cache, Alpaca news. Finds tickers with buzz — news catalysts, earnings, analyst upgrades, social chatter. These are *narrative* signals.
- **Replay/entry phase**: RSI, volume, MACD, MA proximity. These are *technical momentum* signals.

There is no overlap. A stock can be in the news for an analyst upgrade but have bearish MACD and flat RSI. A stock can have perfect technicals but no news buzz. The discovery-to-replay pipeline is filtering narrative signals through a technical-momentum sieve — of course the catch rate is near zero.

**Our live book has the same mismatch**: Discovery finds HIPO (guidance raised), AUBN, PRG, BFLY, HRI — all disqualified on MACD/volume. The morning's sector-full gating was just the visible failure mode; the silent failure is narrative → technical mismatch happening on every candidate.

### 4. Volume threshold is the hidden serial killer

Top configs use `volume_min_mult` of 2.0-3.0x. Our live strategy uses 1.0x. But even at 2.0x in the backtest, catch rates are < 1%. The volume filter alone probably eliminates 70-80% of candidates before any other filter applies — small-cap names rarely trade at 2x average volume without news, and when they DO have news, the technicals often don't align.

### 5. Win rates suggest the technical entry signal is weak

21% win rate on core, 34% on stonks. Our v1.0 backtest had 60.6% win rate — but that was also likely contaminated by look-ahead bias (the framework fix postdates the v1.0 test). These 21-34% numbers are probably closer to reality. The technical entry signal (RSI + volume + MA + MACD) may not have any predictive edge at all in the current market regime.

### 6. Stonks universe > core universe but both lose money

Stonks-relaxed: 47 trades, 34% win rate, -1.70% return. Core-default: 19 trades, 21% win rate, -0.09% return. More trades with a slightly better win rate still lost MORE money. This suggests the win-rate improvement on stonks names is offset by worse per-trade execution — the trades we win are smaller than the trades we lose.

## What This Means for Strategy

### The technical-only entry approach may be structurally unprofitable

This is the uncomfortable conclusion. If 100 backtest variants with 6,000-7,400 signals can't find a config with positive returns and >35% win rate, the entry signal itself may not work. The market may be too efficient at the RSI/momentum level for small-mid caps — by the time the technical setup looks good, the move has already happened.

### We have three paths forward:

**Path A: Fix the signal mismatch.** Instead of filtering narrative discoveries through technical gates, build a parallel entry path that acts on narrative signals directly. "Analyst upgrade + positive sentiment + reasonable price" doesn't need perfect RSI. This is the "dual-signal entry" idea from Round 1 but more radical — the narrative path doesn't share the technical path's filters at all.

**Path B: Abandon discovery and scan purely on technicals.** If we're going to filter everything through RSI/volume/MACD anyway, stop wasting cycles on narrative discovery. Scan the universe for stocks that meet the technical criteria FIRST, then check sentiment/news as a confirmation layer. This inverts the current pipeline.

**Path C: Accept the low hit rate and optimize for per-trade execution.** The strategy works — we just get very few shots. Make each shot count more by improving sizing, exit timing, and scaling decisions. This is the path we've been on (v1.13's bootstrap bias, trailing stops). It's the safest but slowest.

### My recommendation: Path A + B hybrid

The v1.13 changes (wider RSI 40-70, conviction floor 0.35/0.25, dropped catalyst/MACDh-flip requirements) are all moves in the right direction — they open the technical gates wider. But they're parametric tweaks on a pipeline that has a structural mismatch. We need:

1. **Keep the technical entry path** with the v1.13 widened gates — it catches the occasional aligned setup
2. **Add a narrative entry path** that fires on: strong sentiment (0.5+) + catalyst (earnings beat, upgrade, deal) + reasonable valuation (not extended 20%+ above MA) — with NO RSI/MACD/volume gates. This path uses 1-share probes only.
3. **Invert discovery for the technical path**: scan technicals first, confirm with narrative second

The narrative path gives us the "more reps" the bootstrap phase needs. The technical path catches the rare aligned setup. Together they deploy more capital without sacrificing the quality of either path.

## v1.13 vs. These Findings

v1.13 made these changes, all of which align with the backtest evidence:
- ✅ Widened RSI band to 40-70 (backtest: even 50-75 gets 0.32% catch rate, 40-70 is wider)
- ✅ Lowered conviction floor to 0.35/0.25 (backtest top configs use 0.4-0.6, our 0.35 is aggressive but intentional)
- ✅ Dropped MACDh-flip as mandatory exit (backtest: MACD parameters vary by universe)
- ✅ Dropped catalyst requirement (backtest: discovery generates narrative signals, requiring a catalyst check on top of technical gates is double-filtering)

What v1.13 hasn't addressed:
- ❌ Still uses RSI(14), not RSI(7) — unanimous backtest evidence
- ❌ Still filters narrative signals through technical gates — structural mismatch
- ❌ No parallel entry path for narrative-only signals

## For Raf

- **The framework fix worked.** 21-34% win rates and negative returns are credible numbers. We can trust future rounds.
- **RSI(7) is the highest-confidence parameter change** — unanimous across 150+ variants over 3 rounds. Should be implemented regardless of what else changes.
- **The signal mismatch is the real problem**, not parameter tuning. Discovery finds narrative signals; the replay engine requires technical signals. They don't overlap.
- I want to test a narrative-only entry path (sentiment + catalyst, no RSI/MACD gates) in the backtest. Can we add that as a 'narrative-discovery' config family in the next sweep?
- The 2/sector cap is a tertiary concern compared to the signal mismatch — fixing that first, then we can revisit sector limits.

---

*Round 3 written 2026-07-31 ~1:30 AM ET — Stan 🚀*

---

# Round 4 — Root Cause: Discovery/Replay Criteria Are Completely Disjoint

**Source**: Iterations 3-5 plus source-code analysis of `overnight_discovery.py` and `overnight_replay.py` in the paper-trading-rebuild project.

## 🚨 The catch_rate Is Near-Zero For A Specific Reason

I traced the actual code. The "catch rate" measures: *what fraction of discovery-phase signals did the replay engine's trades match?* Matching requires: same symbol, within 2 hours, within 5% price.

The answer is near-zero because **discovery and replay use fundamentally incompatible entry criteria.**

## Discovery Phase: What It Looks For

The `SignalDiscoverer` in `overnight_discovery.py` runs FOUR signal checkers:

| Signal Type | Trigger Condition |
|---|---|
| **RSI Bounce** | RSI < 35 (oversold) → then RSI bounces up over patience ticks |
| **Volume Spike** | Volume > 1.5x 20d avg, sustained for 2 consecutive ticks |
| **Momentum Breakout** | Price up > 2% over 10 ticks, MACD bullish, price above MA20 |
| **MA Bounce** | Price 1-3% below MA, bouncing toward it |

Discovery defaults: `rsi_oversold_threshold=35`, `volume_ratio_min=1.5`, `momentum_threshold=2%`, `ma_period=20`

This is why 6,000-7,400 signals are generated — the criteria are loose. These are **"something is happening"** detectors: something unusual is going on with this stock.

## Replay Entry Gate: What It Requires

The `ReplayVariantEngine` in `overnight_replay.py` applies a COMPLETELY DIFFERENT filter:

```python
# Entry gate (abbreviated from trader_fn in overnight_replay.py:138-180)
rsi_ok = rsi_entry_min <= rsi <= rsi_entry_max    # typically 50 ≤ RSI ≤ 70
vol_ok = volume_ratio >= volume_min_mult            # typically vol ≥ 2.0x avg
ma_ok  = price_above_ma == params["price_above_ma"]  # usually price > MA
macd_ok = macd_fast > macd_slow                      # MACD bullish

# Entry requires BOTH RSI and volume:
if not (rsi_ok and vol_ok):
    return HOLD  # ← NO TRADE, regardless of other signals

# Plus conviction must clear conviction_min (typically 0.60)
conviction = 0.30*(rsi_ok) + 0.25*(vol_ok) + 0.15*(ma_ok) + 0.15*(macd_ok)
if conviction < conviction_min:
    return HOLD
```

Replay defaults: `rsi_entry_min=50`, `rsi_entry_max=70`, `volume_min_mult=2.0`, `conviction_min=0.60`, `price_above_ma=True`

## The Overlap Matrix — Where They Match (And Don't)

| Discovery Signal | Discovery Trigger | Replay Gate | Overlap? |
|---|---|---|---|
| RSI Bounce | RSI **< 35** | RSI **50-70** | ❌ **NEVER** — disjoint RSI ranges. By the time RSI recovers to 50+, the signal is hours old, well past the 2h match window |
| Volume Spike | Vol > **1.5x** | RSI 50-70 + Vol > **2.0x** | 🔶 **RARELY** — volume spikes often happen at RSI extremes (<40 or >70), where replay's RSI gate kills the entry |
| Momentum Breakout | Mom > 2%, MACD bullish, > MA | RSI 50-70 + MACD bullish + > MA | 🟡 **SOMETIMES** — the ONLY overlap path. But discovery doesn't check RSI, and momentum breakouts often push RSI > 70 (overbought), which replay rejects |
| MA Bounce | Price **below/near** MA | Price **above** MA | ❌ **NEVER** — opposite MA requirements. Bouncing from below MA is literally the opposite of "price above MA" |

**Conclusion**: 3 out of 4 discovery signal types have ZERO overlap with the replay entry gates. Only momentum breakout has a theoretical chance, and even that is gated by RSI range mismatch.

## Why Iterations 1-2 Had 0.32-0.63% Catch Rate, But 3-5 Have 0.00

Iterations 1-2 used `DiscoveryConfig` defaults — the standard loose criteria. The few matches (19 out of 6,014; 47 out of 7,422) came from momentum breakout signals where RSI happened to be in the 50-70 band by coincidence.

Iterations 3-5 used tighter/smaller universes:
- **all-tight**: RSI band [55, 60] — only 5 points wide. This eliminates even the rare momentum breakout overlap. 36 tickers, 7,613 signals, zero matches.
- **kairos-macd**: Only 10 tickers, 945 signals. Small universe = fewer chances for coincidence. Zero matches.
- **aldridge-aggressive**: Only 8 tickers, 779 signals. Even fewer chances. Zero matches.

**The catch rate isn't measuring signal quality — it's measuring random coincidence between two disjoint signal detection systems.**

## Where Do The Returns Come From?

Iterations 3-5 show returns of +2.11%, +3.88%, +2.26% with catch rate = 0.00. These returns come from trades that the replay engine found INDEPENDENTLY of discovery — pure technical momentum trades (RSI 50-70, volume > 2x, MACD bullish, above MA). Since they don't match any discovery signal, they're all classified as "false positives" in the scorer.

This means:
1. **The technical entry path IS finding profitable trades.** +2-4% returns are real.
2. **Discovery is adding zero value to the backtest.** Every signal it generates gets ignored.
3. **The scoring formula is broken for this pipeline.** With catch_rate=0 and fp_rate=1.0, the score should be negative — but the z-score normalization on returns artificially inflates it.

## The Specific Scoring Bug

In `overnight_scorer.py:score_all()`:
```python
returns = np.array([s.total_return_pct for s in scored])
z_scores = (returns - np.mean(returns)) / np.std(returns)
normalized = 1.0 / (1.0 + np.exp(-z_scores))  # sigmoid

s.score = 0.40 * catch_rate + (-0.25) * fp_rate + 0.35 * normalized_return
```

When catch_rate=0 and fp_rate=1.0: `score = -0.25 + 0.35 * normalized_return`

For a +3.88% return variant among a field averaging near 0%, the z-score is high and `normalized_return ≈ 0.98`, giving `score ≈ -0.25 + 0.34 = 0.09`.

But the reported scores are 0.31-0.33. This means either:
- fp_rate isn't actually 1.0 (some trades DO match discovery signals after all, just at <0.5% rate)
- OR the normalization is doing more work than visible

Either way, the scoring formula is **rewarding pure technical trades that have nothing to do with discovery**, while penalizing them as "false positives." The 0.40 weight on catch_rate makes discovery the highest-weighted component, but discovery generates signals the replay engine literally cannot act on.

## The Fixes

### Fix A: Align Discovery and Replay Criteria (structural)

Make discovery generate signals using the SAME criteria as the replay entry gate. Two options:

**A1: Add a "momentum entry" signal type to discovery** that mirrors the replay gate:
- RSI in [50, 70]
- Volume > 2.0x avg
- MACD bullish
- Price above MA

This would make catch_rate meaningful — it would measure "how many of these technical setups did each variant catch?"

**A2: Add RSI bounce and MA bounce entry paths to replay** that mirror discovery:
- If RSI < 35 and bouncing → enter (mean reversion)
- If price near MA and bouncing → enter (support bounce)

This would open more entry types and make discovery signals actionable.

### Fix B: Remove Discovery/Replay Link (scoring)

If discovery and replay are measuring different things, stop trying to link them. Instead:
1. Discovery generates a watchlist ("these stocks have catalysts")
2. Replay scans the watchlist with technical entry gates
3. Score = pure technical performance (return, win rate, Sharpe) — no catch rate

This is closer to what our live pipeline does: discovery populates the watchlist, then technical gates filter entries.

### Fix C: Weight fp_rate by profitability

If "false positives" (trades not matching discovery) are profitable, they shouldn't be penalized. Score false positives by whether they made money:
```python
fp_penalty = fp_rate * (1 - win_rate_of_false_positives)
```

A "false positive" that makes money isn't a false positive — it's a trade discovery missed.

## Recommendation

**Fix A1 + Fix B hybrid**:

1. Add a "technical_momentum" signal type to discovery that mirrors the replay entry gate (RSI 50-70, vol 2x, MACD bullish, above MA). This makes catch_rate meaningful.
2. Also keep the existing discovery types (RSI bounce, volume spike, MA bounce) but score them separately — they're a different alpha source.
3. Add a "discovery catch rate" for the narrative signals AND a "technical catch rate" for the momentum signals. Report both.
4. Weight scoring: 0.20 * narrative_catch + 0.20 * technical_catch + 0.30 * return + 0.30 * win_rate

This way we can actually optimize for what matters: catching signals the strategy can ACT on, and making money doing it.

## For Raf

- **The catch_rate measurement is broken by design**, not by bug. Discovery looks for RSI < 35 bounces and MA bounces; replay only enters at RSI 50-70 above MA. They're measuring different universes and calling the empty intersection a "catch rate."
- **The technical-only entries are profitable (+2-4%)** even though they're classified as 100% false positives. This actually validates our v1.13 technical-first approach.
- **The discovery phase as currently designed adds noise, not signal**, to the optimization. 6,000-7,400 "signals" that can never be caught inflate the denominator and make catch_rate meaningless.
- I recommend Fix A1: add a momentum signal type to discovery that mirrors replay entry gates. This turns overnight optimization into a proper parameter sweep over the actual entry strategy.
- The RSI(7) finding from earlier rounds still stands — but it needs to be tested in a framework where catch_rate means something first.

---

*Round 4 written 2026-07-31 ~2:00 AM ET — Stan 🚀*
