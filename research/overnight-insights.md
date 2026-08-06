# Overnight Optimization — 2026-08-04/05 Insights

**Run date**: 2026-08-04 overnight → 2026-08-05
**Scope**: 12 replay configs, ~1.4h total, 0 failures
**Data window**: 2026-06-25 through 2026-08-04

---

## 1. Why is `price_above_ma=False` winning?

The large-caps-macd config with `price_above_ma=False` returned **12.15%** on 13 trades — the best return of any config — while similar configs with the MA gate on returned 4-7%. Across the top 3 by score, two had `price_above_ma=False`.

The MA gate as currently applied (price must be above a moving average to qualify for entry) is filtering out **mean-reversion entries at support**. When a stock pulls back to — or briefly dips below — its MA, that's often the moment of maximum risk/reward. The MA gate says "no entry" and skips the bounce. The winning configs buy the dip.

This aligns with what I've observed live — several of my best names (BL at $27.45 low on Jul 23, CNH repeatedly testing MAs) had their strongest entry signals precisely when price was at or just below a MA, not above it.

**The MA gate is a lazy trend filter that costs more than it saves.** It rejects pullback entries that go on to be winners while keeping the obvious "above MA" setups that everyone can see (and that are often already extended). This is a direct insight from the backtest: the gate is actively destructive.

---

## 2. Catch rate vs. win rate trade-off

| Signal | stonks-hybrid | large-caps-macd | core-momentum |
|--------|---------------|-----------------|---------------|
| Catch rate | 0.79% (highest) | 0.16% | 0.30% |
| Return | -0.41% | **+12.15%** | +7.26% |
| Trades | 57 | 13 | 20 |
| Win% | 31.6% | 38.5% | 35.0% |

stonks-hybrid caught the most signals but lost money — high quantity, low quality. large-caps-macd caught fewer but won more per trade. This is the classic selectivity-over-volume trade-off, but there's a nuance: **the 99% cash idle rate means NONE of the configs are even close to deploying capital meaningfully.** The catch rate debate is secondary when even the "best" configs are leaving 99% of capital idle.

The real question isn't catch rate vs. win rate — it's **how to deploy more capital without sacrificing quality.** Large-caps-macd at 12.15% on 13 trades with 99.8% idle cash is a proof of concept, not a capital allocation strategy. If we could scale that config to 50 trades with the same win rate, the returns would compound dramatically.

**Recommendation**: Don't widen the gates to chase catch rate (that's what ultra-relaxed tried and failed at -0.93%). Instead, widen the **universe size** — more names in the pipeline means the same quality filter finds more entries without lowering the bar.

---

## 3. What signals are we consistently missing?

Looking at the replay data alongside these results, several categories stand out:

- **Pullback-to-MA entries** (the `price_above_ma=False` finding): CNH flipped positive MACDh on Jul 24 while below its 20MA. I bought it then but the MA gate would have filtered it. It went $10.32 → $11.44.
- **Post-earnings volume spikes**: HLN's 3.68x volume spike on Jul 27 preceded a run from $9.94 to $10.27. Our volume gates catch the spike but we wait for confirmation — by the time we confirm, the move is half done.
- **Bounce-from-oversold**: KEX showed a massive reversal on Jul 8 (MACDh +0.068 from -0.595, volume 1.62x). I caught this one, but similar setups on other names were likely missed because the MA gate blocked them.
- **Short-term RSI mean reversion**: The `rsi_period=7` finding across 4 top configs suggests we're using too-long RSI windows and missing rapid reversion signals.

The discovery phase is finding names my current entry gates won't let me trade. The solution isn't weaker gates — it's **different gates**.

---

## 4. Concrete proposal: what to change in the live entry gate

Based on this backtest, I propose three specific changes:

### A. Remove `price_above_ma` as an entry requirement
**Replace with**: price relative to MA as a signal to *weight* conviction, not a binary gate. A stock below its MA with strengthening MACDh gets scored higher (mean reversion premium), not rejected. A stock far above its MA with high RSI gets scored lower (extension discount).

### B. Shorten RSI period from 14 to 7-10
The top configs used RSI 7-10 with entry band 50-70. Shorter RSI reacts faster to reversals and catches momentum earlier. Current RSI 14 is too slow — by the time it moves, the entry is stale.

### C. Test `macd_slow=32` (current is 26)
`macd_slow=32` appeared in 3 top configs including the 12.15% winner. A slower MACD signal line means fewer whipsaws and fewer false flips — especially important given the near-zero oscillation heuristic we already use.

**Combined effect**: Remove the binary MA gate, faster RSI, slower MACD = more entries with better timing, without sacrificing the quality filter that conviction scoring provides.

---

## 5. Should we test `price_above_ma=False` in live trading?

**Yes — immediately.** This is the strongest single finding from the largest backtest run we've done. The evidence is consistent across multiple configs: the MA gate is a net negative.

However, I'd phase it:

- **Day 1-3**: Run with `price_above_ma` removed but watch every "below MA" entry closely. Journal each one separately — price at entry relative to 20MA, 50MA, and outcome.
- **Day 4+**: If below-MA entries perform at or above the existing win rate (~38%), make the removal permanent. No reason to wait for a nightly evolve cycle when the data is this strong.
- **Guardrail**: Keep the hard stop at -6% and the conviction floor at 0.40 — these are the safety nets that don't need changing.

**Expected impact**: Catch rate should increase from sub-1% to the 2-5% range without degrading win rate, because the signals we're adding are the pullback/bounce entries that the MA gate was incorrectly rejecting.

---

## Bonus observation: Cash idle is structural, not gate-driven

The 99% cash idle across ALL 12 configs isn't just tight gates — it reflects the pipeline. We're evaluating 5-6 tickers at a time in replay vs. the live discovery pipeline's 20-30 names. The backtest is constrained by the watchlist size in the replay data. In live trading, a wider discovery pipeline means the same gates produce 3-5x more entries just from having more candidates to evaluate.

**Action**: Feed the watchlist harder. The `discovery_urgency_check` and 45-min cron are working, but the replay constraint means we need more names flowing through the replay pipeline specifically. This is a pipeline engineering task, not a strategy tuning one.

---

_Generated by Stan Hoolihan, 2026-08-04 overnight cycle. To be reviewed and committed before the Aug 5 session._
