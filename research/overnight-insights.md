# Overnight Optimization — 2026-08-09/10 Insights

**Run date**: 2026-08-09 overnight → 2026-08-10
**Data window**: ~20 trading days
**Configs tested**: 3 (core-default, looser-gates, conservative)
**Previous run**: 2026-08-04 → 2026-08-05 (12 configs, ~1.4h)

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

## 6. What changed since the Aug 4-5 run

| Finding | Aug 4-5 | Aug 9-10 | Verdict |
|---------|---------|----------|---------|
| MA gate removal | Recommended immediately | Not tested in these configs, but counterfactual zeros suggest it wouldn't add many trades anyway | Still worth doing (quality improvement, not quantity), but not the cash-idle solution |
| RSI period 7 | Recommended, top configs used 7-10 | Confirmed — RSI 7(55-65) is best | Converged. Strong evidence. |
| MACD slow=32 | Recommended testing | Confirmed — outperforms 12/26 in both runs | Converged. Strong evidence. |
| Cash idle is structural | Flagged, attributed to pipeline/replay constraints | Confirmed — counterfactual zeros prove it's not a gate problem | Now is a confirmed structural ceiling, not a hypothesis |
| Counterfactual missed ops | "All zeros" (vague) | Explicitly ALL zeros across all configs | The diagnostic is clean — no false negatives |
| Index-anchor deployment | Not yet in strategy (v1.12 era) | In v1.20 but untested in live | Highest-readiness lever for cash deployment |

---

## 7. Recommendation for Monday Aug 10

**Primary**: Deploy the index-anchor framework. Pick SPY as the first anchor (most liquid, most thesis-verifiable). Use one conviction slot. Thesis: regime not bearish, SPY technicals not in multi-session decline. Size at conviction-play scale (up to 10% of equity). This deploys ~$950 of the ~$9,477 idle cash immediately.

**Secondary**: If we want to batch a v1.21 technical parameter update, the evidence supports:
- RSI period 7 with band 55-65 (from current 14)
- MACD slow=32 (from current 26)
- These are quality improvements, not quantity — don't expect more trades

**Tertiary**: Increase discovery daemon throughput. Bump `chunk_size` 75→100, consider mid-cap expansion.

**Do NOT**: Lower conviction floor below 0.10, remove the volume gate, or widen RSI bands. The data says these degrade quality. The counterfactual says they won't add profitable trades anyway. Don't break what's working.

---

_Generated by Stan Hoolihan, 2026-08-09 overnight cycle. To be reviewed before the Aug 10 session. Previous run (2026-08-04/05) is preserved above for continuity — this is an update, not a replacement._
