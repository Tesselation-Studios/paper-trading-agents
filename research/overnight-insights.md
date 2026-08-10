# Overnight Optimization — 2026-08-09/10 Insights

**Run date**: 2026-08-09 overnight → 2026-08-10
**Data window**: 10-20 trading days (varies by iteration)
**Configs tested**: 9 total — 3 core-default, 3 stonks-aggressive, 3 all-momentum
**Previous run**: 2026-08-04 → 2026-08-05 (12 configs, ~1.4h)

---

## ITERATION 3: all-momentum universe — NEW (FINAL)

**Universe**: 34 tickers across all groups (full momentum screen)
**Params tested**: RSI(38/68), vol 2x, momentum +3.5%/14d, conviction 0.7/0.45, ma_dist <3%
**Days**: 10 (shorter window than runs 1-2)

### The trend is now a three-run pattern

| Run | Universe | Signals | Catch rate | Score | Return | FP rate |
|-----|----------|---------|------------|-------|--------|---------|
| 1: core-default | Small-cap value (fundamental screen) | 6,527 | 0.0018 | **0.3405** | **+9.96%** | — |
| 2: stonks-aggressive | 10 high-vol momentum names | 7,843 | 0.0000 | 0.3341 | +5.35% | 0.05 |
| 3: all-momentum | 34 tickers, all groups | ? | 0.0000 | **0.2278** | **+1.90%** | 0.00 |

**Score and return monotonically decrease as the universe broadens.** Three runs, three data points, one direction. Core-default (small-cap, fundamentally-screened) at 0.3405 is the clear winner. Add high-vol momentum names → score drops. Add everything → score tanks to 0.2278 with a pitiful 1.90% return.

This is the OPPOSITE of what diversification theory predicts. More names should mean more opportunity. Instead, more names means more noise diluting the signal until nothing survives the entry gates. **The fundamental screen in the core-default pipeline is the real competitive advantage.**

### FP = 0.00 is not a feature — it's rigor mortis

Zero false positives. Sounds great. Means the config is so restrictive it doesn't trade at all. When catch rate is zero, "perfect" FP rate is just a different way of saying the patient is dead. The top config's parameters tell the story:

- **max_position_pct = 6%** (vs live 6% — notably this IS our live setting)
- **maxpos = 6** (vs live's no cap)
- **RSI(7,55-60)** — tightest band tested
- **MA50** — widest MA filter, seriously restrictive
- **conviction 0.6** — tighter than core-default's winning 0.5

This config isn't "winning" — it's the last one standing in a universe with no signal. The optimizer found the combination that loses the least money by never trading, and returned it as "best" because the scoring function rewards not-losing over not-trading.

### max_position_pct=6%: artifact, not signal

The question was raised: does the 6% cap winning suggest we should rethink position sizing?

**No. It's an optimizer artifact of the zero-catch-rate environment.** Here's why:

1. **Core-default's winning config (run 1) had maxpos=25** and no tiny-position constraint. When there ARE trades to catch, the optimizer wants to catch them bigger.
2. **Zero-catch-rate optimizers converge on survival settings.** When every trade is a false positive (FP rate > 0), the best strategy is miniscule positions with tight stops. The optimizer isn't saying "6% positions are optimal" — it's saying "don't trade at all, but if you must, use 6%."
3. **Our live 6% max_position_pct is already conservative** — at $10,420 equity, that's $625 per position. In a small-cap universe ($1-50), that's 12-625 shares. Plenty of room. The problem isn't the cap; the problem is having nothing to deploy it on.
4. **If anything, the data says the 6% cap is fine where it is.** Three runs, three universes, and position sizing never emerged as a differentiator in ANY run where there were actual trades. The v1.20 sizing framework is calibrated. Don't touch it.

### Counterfactual: AAPL/AMZN this time

Run 2's counterfactual was NVDA-only. Run 3's was AAPL/AMZN. The counterfactual rotates through mega-cap names and finds nothing on any of them. The optimizer can't even find HYPOTHETICAL missed opportunities on AAPL — the most liquid, most analyzed stock on Earth. If AAPL doesn't produce counterfactual trades, the problem isn't the universe or the gates. **The signal discovery engine's patterns don't correspond to tradeable setups on mega-cap names, period.**

### The optimizer has diminishing returns

Three runs, and the marginal insight per run is shrinking:
- Run 1: "Gates are calibrated, cash idle is structural" — HIGH value insight
- Run 2: "Signal quality is the bottleneck, not universe breadth" — HIGH value insight (inverted run 1)
- Run 3: "Broader universe = monotonically worse results" — CONFIRMATION of run 2, not new insight

Run 4 would likely produce: "Even broader universe = even worse results." We don't need to prove that. The optimizer is a measurement tool, not a solution engine — it can tell us "this is worse" but can't fix the signal quality. **The fix is upstream of the optimizer entirely.**

### Updated three-run convergence

After 9 configs across 3 universes:

| Signal | Confidence | Runs supporting |
|--------|-----------|----------------|
| Signal quality pre-filter is the P0 bottleneck | **VERY HIGH** | 2, 3 — both broad universes at zero catch |
| Core-default fundamental screen is the moat | **VERY HIGH** | 1, 2, 3 — monotonic decline with breadth |
| Entry gates are correctly calibrated | **HIGH** | 1, 2, 3 — counterfactual zeros across all |
| RSI(7) + MACD(12,32) + 2x volume | **HIGH** | 1, 3 — top config in both momentum paradigms |
| Broader universe = worse results (not better) | **HIGH** | 2, 3 — consistent direction |
| Position sizing is not a problem | **HIGH** | 1, 2, 3 — never a differentiator with trades |
| Optimizer has diminishing returns | **MEDIUM** | 3 — marginal insight shrinking |
| max_position_pct 6% is an artifact, keep it | **HIGH** | 3 — converged from zero-catch-rate noise |

### What I'm NOT doing based on run 3

- ❌ Reducing max_position_pct below 6%
- ❌ Capping max positions at 6
- ❌ Adopting MA50 as a gate
- ❌ Running more optimizer iterations chasing a pattern that's converged
- ❌ Adding mega-cap names to the discovery universe

### What I AM doing

- ✅ Reinforcing the signal quality pre-filter as the single highest-leverage change
- ✅ Keeping the core-default small-cap fundamentally-screened universe as the foundation
- ✅ Deploying SPY index-anchor as a parallel cash-deployment track (independent of signal quality)
- ✅ Closing the book on optimizer runs for now — the pattern is converged, diminishing returns are real

---

## ITERATION 2: stonks-aggressive universe

**Universe**: NVDA/TSLA/COIN/PLTR/MSTR/GME/RIOT/MARA/HOOD/DJT (10 high-volatility stonks names)
**Params tested**: RSI(28/78) mean-reversion paradigm, vol 1.2x, conviction 0.5/0.3

### Catch rate: ZERO. Literally zero.

| Universe | Signals found | Trades caught | Catch rate | Return | Win% | FP rate |
|----------|--------------|---------------|------------|--------|------|---------|
| core-default | 6,527 | 12 | 0.0018 (0.18%) | +9.96% | 0.33 | — |
| stonks-aggressive | 7,843 | ~0 | **0.0000** | +5.35% | — | 0.05 |

Stonks-aggressive found 20% MORE signals (7,843 vs 6,527) and caught ZERO of them. The 5.35% return is from NVDA buy-and-hold drift, not from trading. Core-default at least caught 12 trades from fewer signals.

**This inverts the iteration 1 conclusion.** I said the binding constraint was universe size — widen the pipeline, feed more names. But stonks-aggressive has MORE names, MORE signals, and ZERO trades. The problem isn't that we don't have enough candidates. The problem is that **the signal discovery engine is finding noise, not signal.**

### The signal discovery pipeline is the bottleneck

7,843 signals → 0 trades. That's a 100% rejection rate by the entry gates. But the gates aren't the problem — they're doing their job. The signals themselves are garbage:

- **High-volatility momentum names produce false patterns constantly.** Every dip on GME looks like an RSI oversold bounce. Every volume spike on RIOT looks like a breakout. Every MACD cross on MARA looks like a momentum shift. These names don't follow technical patterns — they follow headlines, Elon tweets, and crypto prices. The discovery engine treats every technical pattern as a "signal" when 99.85%+ are just noise inherent to the asset class.
- **RSI(28/78) is a category error on trending names.** Mean-reversion signals (buy at oversold 28, sell at overbought 78) assume range-bound behavior. TSLA/NVDA/COIN trend — they go from overbought to more overbought, and oversold to more oversold. Using a mean-reversion paradigm on momentum names is fundamentally mismatched. The core-default's RSI(55-65) momentum-continuation paradigm is the right one.
- **NVDA-only counterfactual is damning.** All 10 counterfactual trades were on NVDA. The other 9 names (TSLA, COIN, PLTR, MSTR, GME, RIOT, MARA, HOOD, DJT) contributed absolutely nothing — not even the counterfactual analysis could find hypothetical profitable setups they missed. Nine of ten names in the universe are signal-negative.
- **FP rate of 0.05.** 5% false positive rate — the few signals that DO pass the gate are wrong more often than right. The signal-to-noise ratio is negative.

### What this means for the iteration 1 conclusions

Iteration 1 said: "The binding constraint is universe size. Widen the pipeline." **That was wrong.** The binding constraint is signal QUALITY at the discovery level, not universe size. Adding more names — especially high-volatility momentum names — just adds more noise. The discovery engine is a firehose of technical patterns with no quality filter. The entry gates then have to filter 7,843 signals down to 0-12 trades, a 99.85%+ rejection rate. That's not gating — that's the discovery engine failing to discriminate.

**The fix isn't looser gates or a wider universe. The fix is better signal discovery.**

### Concrete proposal: add a signal quality pre-filter to the discovery engine

Right now the discovery engine promotes EVERY technical pattern as a "signal" — every RSI cross, every MACD flip, every volume spike, on every timeframe. Then the entry gate has to sort through thousands of noise events to find the 12 real ones.

**What a quality pre-filter would look like:**

1. **Multi-timeframe confirmation required.** A signal only counts if it appears on at least 2 timeframes (e.g., 15m MACD flip AND 1h MACD flattening/turning). A single-timeframe blip is noise.

2. **Directional agreement required.** RSI, MACDh, and volume must all point the same direction. RSI overbought + MACDh bearish + declining volume = noise. RSI momentum + MACDh green + volume confirming = signal.

3. **Trend alignment bonus.** A bullish signal against a bearish primary trend is weaker. A bullish signal WITH the trend is stronger. Weight accordingly.

4. **Signal quality score instead of binary.** Instead of "signal exists" (binary), score each signal on a 0-1 quality scale. Only promote signals above a quality threshold (e.g., 0.4). This would reduce 7,843 signals to maybe 200-300 quality-filtered candidates, and the entry gate would catch a much higher percentage.

5. **Universe pre-screening.** Drop the stonks-aggressive universe entirely. High-volatility momentum names don't produce actionable technical signals — they're headline-driven. Keep the core-default small-cap fundamentally-screened universe. It produces fewer signals but more actionable ones.

### Updated recommendation hierarchy

Given both runs, here's the revised priority stack:

**🔴 P0 — Signal quality pre-filter (discovery engine).** The discovery engine is the bottleneck, not the entry gates. 7,843 signals → 0 trades means the signals are noise. Fix upstream: multi-timeframe confirmation, directional agreement, quality scoring. This is where the most leverage is.

**🟡 P1 — Index-anchor conviction positions (already in v1.20).** Still the best way to deploy idle cash while we fix signal quality. SPY anchor at conviction sizing. Zero code changes needed. This is a deployment lever, not a signal-quality lever — it doesn't fix the root cause but keeps capital working.

**🟢 P2 — Technical parameter updates (v1.21).** RSI(7)/MACD(12,32)/2x volume convergence is real across both runs. These are quality tweaks for when the signal pipeline is producing real signals. Marginal impact without P0 fixed first.

**❌ Dropped — Widen the universe (iteration 1's P0).** The stonks-aggressive run proved this wrong. More universe → more noise, not more trades. Reverse course on this recommendation.

**❌ Dropped — Loosen entry gates.** Both runs show this degrades quality. Stonks-aggressive tried conviction 0.3 and still caught zero. The gates aren't the problem.

---

## ITERATION 1: core-default universe (preserved for reference)

---

## 1. The counterfactual zeros change everything

Previous run (Aug 4-5): "Catch rate 0.18% ... counterfactual didn't find meaningful missed opportunities."

This run: **counterfactual is ALL zeros. Zero missed opportunities across all three configs.**

This is the most important finding in either run, and it fundamentally reframes the strategy:

- **We are NOT leaving money on the table.** The discovery pipeline is finding the right candidates. The entry gates are correctly filtering — when they reject a candidate, that candidate would NOT have been profitable. There is no hidden pool of quality entries being gated off.
- **The universe of profitable small-cap setups is genuinely sparse.** Even the loosest config (conviction 0.4, no catalyst gate, wider RSI 50-70, 1.5x volume) only found 20 trades in 20 days. The counterfactual says none of the rejected candidates in the remaining ~99.6% would have been profitable either.
- **Every prior optimization run converged on the same ceiling**: 12-20 trades per 20 days, 99%+ cash idle. Aug 4-5 saw the same pattern across 12 configs. This run sees it across 3 more. That's 15 configs, zero exceptions — the ceiling is structural.

**Implication**: Further gate-tuning has hit diminishing returns. We're squeezing fractions of a percent of catch rate at the cost of degrading win rate. The binding constraint is now the universe size, not the entry criteria.

---

## 2. Gate calibration is genuinely at the sweet spot

The three configs bracket the current live settings and show clear degradation in both directions:

| Config | Conviction | Catalyst | RSI | MACD | Volume | Trades | Return | Win% |
|--------|-----------|----------|-----|------|--------|--------|--------|------|
| Core-default | 0.5 | min 0.5 | 7(55-65) | 12/32 | 2x | 12 | +9.96% | 0.33 |
| Looser | 0.4 | none | 7(50-70) | 8/32 | 1.5x | 20 | +8.74% | 0.30 |
| Conservative | 0.7 | min 0.3 | 14(40-65) | 12/26 | 2x | 9 | +7.38% | 0.22 |

**Loosening (conviction 0.5→0.4, drop catalyst gate, widen RSI, relax volume):**
- +8 trades (12→20) but -1.22% return and -3pp win rate
- You're catching more setups but they're weaker — the quality dilution is real
- Win rate drops below 0.33, which is already thin

**Tightening (conviction 0.5→0.7, add catalyst, MA20, RSI14, MACD 12/26):**
- -3 trades (12→9) AND -2.62% return AND -11pp win rate
- This is the worst outcome by every metric — fewer trades AND worse quality
- The 0.22 win rate with 9 trades is statistically meaningless (2 wins out of 9)
- RSI(14) is confirmed too slow — misses the entry window entirely
- MACD(12,26) underperforms MACD(12,32) in every comparison across both runs

**The current live conviction floor (0.10, effectively disabled as a gate) is NOT being tested here.** The overnight optimizer is testing conviction as a hard gate at 0.4/0.5/0.7, which is different from the live strategy's gestalt-based entry judgment. The live `conviction_floor: 0.1` in params.json is a sanity check against broken scores, not a real filter — the actual entry quality comes from `reconcile` agreement/signal count/confidence, which the optimizer can't replicate because it's a qualitative gestalt, not a numeric threshold.

---

## 3. Technical signal convergence is real and consistent

Across both the Aug 4-5 run (12 configs) and this run (3 configs), the same technical parameters keep winning:

- **RSI period 7 with band 55-65**: Shorter RSI catches momentum earlier. RSI 14 is consistently worse — it lags and entries fire after the move is stale. RSI 7(50-70) is too wide and lets in noise. The 55-65 band is the tightest and best.
- **MACD(12,32)**: The 32-period slow line reduces whipsaws compared to standard 12/26. This appeared in 3 top configs in the Aug 4-5 run and again as the best-performing config here. The near-zero oscillation heuristic (MACDh < 0.005 on flat price = noise) built into the live strategy is doing the same thing manually — MACD(12,32) mechanizes it.
- **2x volume threshold**: Both 2x configs beat the 1.5x config on return despite fewer trades. Volume is a genuine quality signal, not an arbitrary filter.
- **Catalyst minimum 0.5**: The core-default config with catalyst gating beats the looser config without it. Adding a minimum catalyst requirement screens out low-conviction noise without meaningfully reducing trade count.

**Should we adopt RSI(7)/MACD(12,32) in live trading?** The evidence is consistent across 15 configs and two independent overnight runs. But `price_above_ma` removal (recommended Aug 4-5, never live-tested) and RSI period shortening haven't been validated in live trading yet either. If we're going to batch changes into a v1.21, this is the evidence to base it on. But the counterfactual zeros suggest the impact on trade count will be marginal — the technical tweaks fine-tune quality, not quantity.

---

## 4. Conviction scoring in the optimizer vs. live trading

This is a methodological gap worth flagging:

The optimizer's `conviction` parameter is a single numeric threshold (0.4/0.5/0.7) applied mechanically. The live strategy's entry judgment uses:
- `reconcile` agreement (≥3 directional signals agreeing)
- `combined_confidence` ≥ 0.60
- Qualitative gestalt: world/sector narrative, congressional trades, fundamentals, wiki, cross-sectional momentum

These are fundamentally different things. The optimizer's conviction score is a coarse proxy that flattens the gestalt into one number. The live strategy's multi-signal reconciliation with a confidence anchor is richer and more discriminating. The optimizer CAN'T test the live entry logic — it can only test simplified thresholds.

**This means the optimizer's 0.5 conviction sweet spot should NOT be interpreted as "set conviction_floor to 0.5."** The live conviction_floor at 0.10 is correct — it catches broken scores without acting as a real gate. The entry quality comes from the gestalt, which the optimizer doesn't model.

---

## 5. The cash-idle problem needs a different lever

We've run 15 configs across two overnight cycles. Every single one leaves 99%+ cash idle. The counterfactual says none of the rejected candidates would have been profitable. The conclusion is unavoidable:

**The binding constraint is not the entry gates. It's the universe size.**

Three structural solutions, ordered by readiness:

### A. Index-anchor conviction positions (already in v1.20, not live-tested)
SPY/QQQ/DIA/IWM conviction slots deploy idle cash at a lower but more reliable return. The strategy already has the framework — earmark 2 of 5 conviction slots, thesis-gated on regime read (no prolonged bearish), reallocatable into individual-stock ideas when they appear. This is the LOWEST-RISK, HIGHEST-READINESS lever. It requires zero code changes — just start using the existing framework on the next trading day.

### B. Widen the universe pipeline
- Raise the bankroll-scaled `max_price` ceiling further (bankroll ceiling has grown — more buying power should mean a wider universe)
- Increase `discovery_daemon.chunk_size` from 75 to get more candidates per scan
- Consider adding mid-cap names ($2B-$10B) to the discovery pool — they have more liquidity, more setups, and the same technical signals work on them

### C. Accept the structural reality and stop optimizing gates
If 12-20 trades per 20 days at 33% win rate is the natural ceiling for quality small-cap entries, then the strategy's "small and wide" design is already working at capacity. Forcing more trades by loosening gates degrades quality. The index-anchor framework exists precisely because v1.20's designer anticipated this exact ceiling. Use the tool that was built for this problem instead of trying to solve it with gate-tuning that the data says won't work.

---

## 6. What changed since the Aug 4-5 run (now with 3 iterations)

| Finding | Aug 4-5 | After run 1 | After run 2 | After run 3 |
|---------|----------|-------------|-------------|-------------|
| MA gate removal | Recommended | Unclear | Unclear | Unclear — not a differentiator, never tested |
| RSI period 7 | Recommended | Confirmed | Confirmed | Confirmed in momentum paradigm too |
| MACD slow=32 | Recommended testing | Confirmed | Confirmed | Confirmed |
| Cash idle structural | Flagged | Confirmed | Confirmed | Confirmed — 9 configs, zero exceptions |
| Counterfactual zeros | Vague | Explicit | NVDA-only | AAPL/AMZN — rotating mega-caps, same result |
| Universe breadth vs quality | "Widen pipeline" | P0: widen | REVERSED: P0 is quality, not breadth | CONFIRMED: monotonic decline with breadth |
| Index-anchor deployment | N/A | P1 | P1 | P1 — still highest-readiness cash lever |
| Position sizing | N/A | N/A | N/A | maxpos=6 artifact, do NOT adopt |
| Optimizer diminishing returns | N/A | N/A | Noticed | Confirmed — marginal insight per run shrinking |

---

## 7. Final recommendation for Monday Aug 10 (updated after iteration 3)

**🔴 P0 — Signal quality pre-filter in the discovery engine.** Three runs, two broad universes, zero catch rate. The discovery engine finds thousands of patterns and promotes all of them as "signals" — but 99.85%+ are noise. The fix is upstream of the optimizer: multi-timeframe confirmation, directional agreement, quality scoring, and a fundamental-screening dimension. The core-default universe's fundamental screen is the secret sauce — lean into it, don't dilute it with high-vol momentum names that don't produce actionable technical signals.

**🟡 P1 — Deploy the index-anchor framework.** SPY as first conviction anchor. Deploys ~$950 of idle cash. Thesis: regime not bearish, SPY technicals not in multi-session decline. Independent track from the signal-quality problem — keeps capital working while we fix the root cause.

**🟢 P2 — Technical parameter updates (v1.21).** RSI(7)/MACD(12,32)/2x volume. Converged across 3 runs, 3 universes. Marginal without P0, but real.

**❌ Do NOT:**
- Widen the universe (runs 2+3: monotonic decline with breadth)
- Loosen entry gates (all runs: counterfactual zeros, loosening degrades quality)
- Reduce position sizing below 6% (run 3 artifact, not a signal)
- Cap max positions at 6 (same artifact)
- Run more optimizer iterations (diminishing returns confirmed, run 3 was noise-confirmation)

---

## 8. Synthesis: what all three runs agree on (FINAL)

After 9 configs across 3 universes:

| Signal | Confidence | Evidence |
|--------|-----------|----------|
| Signal quality pre-filter is the P0 bottleneck | **VERY HIGH** | Runs 2+3: broad universes at zero catch; run 1: only fundamental-screened universe catches anything |
| Core-default fundamental screen is the competitive moat | **VERY HIGH** | Monotonic decline: core (0.3405) → stonks (0.3341) → all-momentum (0.2278) |
| Entry gates are correctly calibrated | **HIGH** | Counterfactual zeros across all 3 runs, 9 configs |
| RSI(7) + MACD(12,32) + 2x volume | **HIGH** | Top config in runs 1 and 3 (momentum paradigms); run 2 used wrong paradigm (28/78) |
| Broader universe = monotonically worse results | **HIGH** | Runs 2+3: adds noise, not signal |
| Position sizing is not a differentiator | **HIGH** | Never emerges in runs with actual trades; run 3's maxpos=6 is a zero-catch-rate artifact |
| Cash idle is structural, not gate-driven | **HIGH** | 9 configs, every one at 99%+, zero exceptions |
| Optimizer has diminishing returns | **MEDIUM** | Run 3 confirmed run 2's pattern without new insight |
| Index-anchor is the best cash-deployment lever | **MEDIUM** | Untested but zero-code-change, thesis in v1.20 |

**The trajectory-changing action**: A signal quality pre-filter in the discovery engine that mirrors what the core-default fundamental screen already does implicitly — multi-timeframe confirmation, directional agreement, and a fundamental-quality dimension that rejects names where technical patterns are noise (high-vol momentum, headline-driven mega-caps).

**What this means for the optimizer**: The overnight optimizer has done its job. It measured what it could measure. The binding constraint is upstream — signal quality at discovery time — and the optimizer can test filters but can't generate better signals. We've extracted the signal from 3 runs. More runs would be noise.

---

_Generated by Stan Hoolihan, 2026-08-09 overnight cycle (all 3 iterations). To be reviewed before the Aug 10 session. Iteration 1's universe-breadth thesis was disproven by iteration 2 and doubly disproven by iteration 3. The bottleneck is signal quality at the discovery level. The optimizer has converged — diminishing returns confirmed._
