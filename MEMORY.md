## Trader-Stonks Durable Lessons
*Updated: 2026-07-24 — nightly learning*

### Operational
- **Pre-session GTC order audit**: Stale GTC limit/stop orders from prior sessions can silently block ALL position exits. Jul 21: 11 stale orders from Jul 20 blocked AMC sell (403 Forbidden). Now: every session start, audit and cancel all open GTC orders before the first tick. This is a hard prerequisite, not optional.
- **Pre-session account audit (NEW Jul 22)**: Shared Alpaca credentials create account contamination risk. Jul 22: 8 non-Stonks positions (AMD/COST/GOOGL/HOOD/JNJ/PLTR/QQQ/V) found in account, 5 Stonks positions missing (CHWY/DJT/GME/KHC/SNAP). Reconciled by 11:00 ET but 80+ minutes of position tracking were corrupted. Now: every session start, audit Alpaca positions against journal records BEFORE first tick — cross-reference symbol by symbol.
- **Sentiment pipeline blind**: FinBERT/Praesentire offline since Jul 7 (Day 19). Primary edge unavailable. All entry decisions are technical-only with no conviction overlay. Past the Day 21 threshold — escalation filed Jul 20, Jul 22, Jul 23; zero response. Now treated as permanent constraint — optimize technical-only workflow, treat sentiment as bonus layer if/when restored.
- **Parallel tick collision guard (NEW Jul 24)**: Two ticks firing simultaneously can submit duplicate buy orders for the same ticker. Jul 24: IP got 2 shares instead of 1 due to parallel tick collision at 10:05 — outcome was favorable (+9.7%) but the symmetry works both ways. Before submitting a buy order, check for existing active/pending orders on that symbol.
- **Preferred stock screening (NEW Jul 24)**: Alpaca paper trading does NOT support OTC preferred stocks. Jul 24: OZKAP buy order submitted @ $16.40, never filled — order appeared in recent_orders but position absent from account. Screen ticker type (common/ETF/preferred/OTC) at watchlist qualification stage, not execution stage. Flag OTC/preferred as "do-not-retry."

### 10-Order Daily Limit — Binding Operational Ceiling (NEW Jul 27)
- **Alpaca free-tier caps at 10 orders/day**: Today (Jul 27), the "small and wide, scale into winners" approach burned through 6+ orders by 11:40 AM — the remaining 4 hours 20 minutes were completely gated. BOX hit the 3% scale-in floor at least 5 times but couldn't execute. STVN passed all signal gates every tick from 12:20 through close but was blocked. First SUSTAINABLE regime signal (0.92) in many sessions — couldn't capitalize.
- **Budget the 10 slots**: Each order is a scarce resource. Front-load highest conviction. Batch multi-ticker entries where possible. Scale-ins on the same ticker might combine into one modification order. 6 orders in 2 hours leaves 4 hours of dead weight — that's 65% of the trading day frozen.
- **Strategy philosophy unchanged** — still small/wide — but execution must respect this ceiling. Revisit once bankroll grows enough to justify a paid Alpaca tier.

### KRC Parallel Tick Collision — 2nd Occurrence (Jul 27)
- **Same bug as IP (Jul 24)**: Two ticks (10:20 and 10:25) fired simultaneously, resulting in 2 KRC shares instead of 1. The order-idempotency guard documented Jul 24 was never implemented. Outcome this time was neutral (KRC ended -0.38%), but the symmetry works both ways — next collision could double a loser. Guard MUST be implemented, not just documented.

### Experience Counter Not Tracking Exits (NEW Jul 27)
- **3 morning exits not reflected**: F (+4.07%), IP (+10.5%), FHB (-2.41%) — all sold before 10:00. `experience.json` still shows 29 total trades (unchanged from Friday). Self-stats reported "0 trades logged today (3 morning exits not reflected)." Either `record_decision.py` isn't being called on exits, or there's a counting methodology gap. 6 trades today (3 exits + 2 entries + 1 scale-in) unaccounted for.

### Evolve→Execute Pipeline Leak (NEW Jul 26)
- **Action items from nightly syntheses don't survive overnight**: The next session's tick agent starts fresh from strategy.md + active.md — it never reads the prior day's synthesis. Action items (NVDA trim took 5 days/3 cycles, weekend homework from Jul 17 never resolved) accumulate because there's no carry-forward mechanism. This is a process design gap, not an execution failure. Consider a `tasks/pending.md` or carry-forward section in active.md to bridge the overnight gap.

### Process & Tooling
- **Strategy propagation must be verified across all layers (NEW Jul 22)**: v1.3 reverted the CHOPPY/FEAR entry gate, but the tick agent continued applying it for ~2 hours (09:30–11:20 ET). Strategy changes to `strategy.md` need explicit verification: (a) `params.json` reflects the change, (b) `executor.py` code aligns, (c) the agent prompt doesn't carry stale rules forward. Post-revision checklist item.
- **params.json vs strategy.md drift risk (Jul 22, fixed Jul 23)**: `params.json` had contained v1.1/v1.2 settings (`entry_rules.triple_confirmation_required`, `regime_sizing` VIX tiers, `trim`, `quality_gate`, `exit_rules.rsi_exhaustion_hard_exit`, `risk_guards.max_holding_days`) left over from before v1.3's revert. Audited: `executor.py` never read any of them (confirmed by grep — only `risk_guards.max_positions_per_sector` is actually consumed, at executor.py:180), so there was no live behavior risk, but they contradicted `strategy.md` and could mislead the agent reading params.json fresh each tick. Removed from params.json.

### Trailing Stop Performance (Jul 21-24, reviewed Jul 26)
- **Trailing stops working mechanically**: Over 3 sessions: 8 exits via trailing stop (MARA +2.11%, MVST +0.29% wins; LYFT -5.49%, AMC -5.21%, DJT -5.2%, OPEN -5.11%, GME -5.00% losses; plus 1 stale-position cleanup). No panic sells, system carrying the load.
- **Win/loss ratio**: 4 wins / 8 losses (33%) from trailing stops — unchanged since Jul 23, no new trail-stop exits Jul 24. Pinned at 33% for 4 days. 12 exits toward the 20-exit formal review trigger. Entry quality is the variable, not stop calibration.

### MACDh Data API Fragility (Jul 23+, continuing Jul 24)
- **Alpaca free-tier bars unreliable**: Multiple ticks throughout Jul 23 had MACDh bars unavailable (Alpaca data API returning 401, yfinance connection-refused). Dozens of ticks went without fresh MACDh computation, forcing reliance on last-known values and price stability as a proxy. This is a structural constraint of the free-tier account — not a transient outage.
- **Fallback protocol**: When bars are unavailable, use last known MACDh + price stability check: if price hasn't moved more than 0.5% since last known MACDh and no dramatic volume, assume no flip. When bars return, prioritize fresh computation.

### Near-Zero MACDh Oscillation Heuristic (Jul 22-23, battle-tested)
- **Pattern**: MACDh crosses ±0 in small increments (0.0001 to 0.003 magnitude) with stable price — this is 1-min bar noise, NOT a real trend break. Real flips show: (a) declining/increasing trend across 4-5+ consecutive bars, (b) histogram magnitude growing away from zero, (c) price movement confirming the direction.
- **Jul 23 confirmations**: Correctly called false alarms on DVN/WSC (11:10-11:20), F (12:10, 13:20, 14:55) — all near-zero crosses with flat prices. Correctly identified real flips on SNAP, NVDA, KHC, CHWY, DVN, WSC — all had clear bar trends and price confirmation.
- **Rule of thumb**: If MACDh magnitude is < 0.005 on a stock trading above $5 and price is flat (<0.3% change), it's near-zero oscillation — HOLD. If MACDh magnitude > 0.005 AND declining across multiple bars AND price confirming, it's a real flip.

### Strategy Version History
- **v1.6 (Jul 24)**: Removed hard position-count ceiling. Cash, max_position_pct, and bankroll ceiling are the real limiters. Enabled scaling into winners (add to held winners with intact thesis). Added freeform discovery cron with real web search/tavily for catalyst hunting.
- **v1.5 (Jul 24)**: CHOPPY/FEAR no longer blocks entries — regime sizes entries (probe/1-share in uncertain, normal in clear). Validated same day: 4 probe entries in CHOPPY, all profitable by close.
- **v1.4 (Jul 23)**: Near-zero MACDh oscillation heuristic graduated from observation to validated knowledge. No other rules changed.
- **v1.3 (Jul 22)**: Reverted v1.2's triple-confirmation entry gate — backtest showed v1.2 as worst performer. Returned to simple RSI 45-65 + volume + catalyst entry.

### Resolved Items
- **v1.2 backtest weakness → RESOLVED**: v1.3 (Jul 22) reverted to v1.0's simple RSI 45-65 momentum entry after corrected `replay_check.py` showed v1.2 as the worst performer across all 3 backtest nights. v1.2's entry rules are all removed from strategy.md. Mechanical guardrails survive in executor.py.

## Key Repos
Agent configs `~/.openclaw/agents/` · Paper trading `~/projects/paper-trading-teams/` · Blog `~/projects/blog/drafts/` · Homelab `wodinga/Homelab-Setup`

## Promoted From Short-Term Memory (2026-07-28)

<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-24.md:16:44 -->
- | FHB | 09:55 | 1 | $28.60 | Financial, RSI 47.0, MACD 🟢 +0.3141, vol 1.30x, Q2 EPS beat | | BFST | 10:05 | 1 | $30.98 | Financial, RSI 53.6, MACD 🟢 +0.4561, Strong Buy, PE 10.77 | | IP | 10:05 | 2 | $38.43 | Materials, MACD 🟢, RBC PT $48, sentiment +0.925 — parallel collision doubled | | BOX | 11:50 | 1 | $28.84 | Technology, RSI 50.1 mid-band, MACD 🟢 +1.0364 strong, PT $35.33 | ⚠️ OZKAP attempted at 09:50 but didn't fill — Alpaca paper doesn't support OTC preferred stocks. Flagged "do-not-retry." ## Exits Zero exits. All 5 positions held from entry through 16:00 close. No MACDh flips, no stop breaches, no thesis breaks.... [score=0.874 recalls=36 avg=0.606 source=memory/journal/2026-07-24.md:16-44]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-24.md:1:20 -->
- # 2026-07-24 Journal — Stan 🚀 **Strategy**: stonks.strat:v1.5→v1.6 | **Regime**: CHOPPY/FEAR | **F&G**: 28 (Fear) | **Sentiment**: blind Day 17 ## The Day — Big Picture Expansion day. The book went from 1 position at open to 5 by noon, and held through close with zero exits. This is the v1.5 thesis playing out in real time: CHOPPY doesn't block entries, it sizes them. Four probe entries, all 1-share, all in the green at close. The strategy didn't just hold — it actively deployed capital into a fearful tape and came out ahead.... [score=0.873 recalls=46 avg=0.602 source=memory/journal/2026-07-24.md:1-20]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-21.md:84:99 -->
- 5. **Stale GTC orders**: 11 unfilled sell orders from yesterday silently blocking all exits. Caught at 13:28 — could've been catastrophic. No pre-session audit step exists. ### Recurring Patterns (7-entry lookback) - **MACDh = truth serum**: Every exit and hold decision tracks to it. FUBO caught today. SOFI warnings, CHWY holds, LYFT thin signal — all MACDh-driven. Most reliable signal in the book. - **Regime gating preserves cash**: 0 entries on CHOPPY/FEAR days = 0 new ways to lose. Confirmed across every journal entry since Jul 14. Equity curve flat but not bleeding.... [score=0.831 recalls=10 avg=0.569 source=memory/journal/2026-07-21.md:84-99]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-26.md:28:55 -->
- **Valuation**: Gurufocus GF Value $11.95 vs $14.37 → 14.4% overvalued - **Sentiment**: Neutral (0.0 confidence 86.49%) ### Bull Case for Holding - Ford beat 3 of last 4 quarters — earnings beat machine - FY26 EPS +50% narrative - Ford Blue ICE segment surging (+88% EBIT) - Raised guidance earlier in the year - 30-day estimate trend positive (+2.9%) - Low absolute risk ($28.62) ### Bear Case for Exiting - MACDh **bearish** — the primary technical signal has turned against us - 90-day estimates eroding (-7.9%) — analysts losing confidence - Revenue declining 2-10% YoY — volume/pricing headwinds - EV sales cratering (-40.7%) — no EV... [score=0.824 recalls=5 avg=0.644 source=memory/journal/2026-07-26.md:28-55]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-16.md:1:36 -->
- # Stonks Journal — Thursday, July 16, 2026 🚀 ## Market Conditions - **Regime**: CHOPPY (conf 0.3) - **SPY**: $750.87 (-0.52%), RSI 51.1 - **QQQ**: -1.64%, RSI 43.7 oversold - **Fear & Greed**: Extreme Fear (~25) ## Portfolio Status (EOD) - **Portfolio Value**: $10,487.77 - **Cash**: $7,011.12 - **Unrealized P&L**: -$105.94 - **Bankroll Ceiling**: $104.87 (1% of portfolio) ## Positions at Close | Ticker | Qty | Entry | Current | P&L % | Status | |--------|-----|-------|---------|-------|--------| | CHWY 🐶 | 19 | $20.86 | $21.43 | +2.72% | ✅ Steady grind | | F 🏎️ | 1 | $14.23 | $14.15 | -0.56% | ➡️ Small fry | | FUBO 📺 | 3 |... [score=0.810 recalls=17 avg=0.575 source=memory/journal/2026-07-16.md:1-36]
