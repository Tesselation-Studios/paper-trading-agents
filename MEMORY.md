## Trader-Stonks Durable Lessons
*Updated: 2026-08-02 — weekly review*

Durable, resolved lessons live here. Open, unresolved follow-ups (a proposed fix, an escalation, something worth doing but not done yet) live in `tasks/pending.md` — check it at the start of every reflection session.

### Operational
- **Pre-session GTC order audit**: Stale GTC limit/stop orders from prior sessions can silently block ALL position exits. Jul 21: 11 stale orders from Jul 20 blocked AMC sell (403 Forbidden). Now: every session start, audit and cancel all open GTC orders before the first tick. This is a hard prerequisite, not optional.
- **Pre-session account audit (NEW Jul 22)**: Shared Alpaca credentials create account contamination risk. Jul 22: 8 non-Stonks positions (AMD/COST/GOOGL/HOOD/JNJ/PLTR/QQQ/V) found in account, 5 Stonks positions missing (CHWY/DJT/GME/KHC/SNAP). Reconciled by 11:00 ET but 80+ minutes of position tracking were corrupted. Now: every session start, audit Alpaca positions against journal records BEFORE first tick — cross-reference symbol by symbol.
- **Sentiment pipeline blind**: FinBERT/Praesentire offline since Jul 7 (Day 27). Primary edge unavailable. All entry decisions are technical-only with no conviction overlay. Escalation filed Jul 20, Jul 22, Jul 23; zero response. Now treated as permanent constraint — optimize technical-only workflow, treat sentiment as bonus layer if/when restored.
- **Parallel tick collision guard (NEW Jul 24)**: Two ticks firing simultaneously can submit duplicate buy orders for the same ticker. Jul 24: IP got 2 shares instead of 1 due to parallel tick collision at 10:05 — outcome was favorable (+9.7%) but the symmetry works both ways. Before submitting a buy order, check for existing active/pending orders on that symbol.
- **Preferred stock screening (NEW Jul 24)**: Alpaca paper trading does NOT support OTC preferred stocks. Jul 24: OZKAP buy order submitted @ $16.40, never filled — order appeared in recent_orders but position absent from account. Screen ticker type (common/ETF/preferred/OTC) at watchlist qualification stage, not execution stage. Flag OTC/preferred as "do-not-retry."

### 10-Order Daily Limit Was a Phantom — DEBUNKED Jul 28
- **The "10 orders/day" cap was NEVER real**: Alpaca's actual constraint is a 200 req/min API rate cap — not a daily order limit. The 10-order figure originated as a self-imposed guardrail (`params.json: risk_guards.order_count_audit_threshold_daily`) that got miscategorized in MEMORY.md as an Alpaca rule, then propagated into the tick agent's manual counting as gospel. The actual params.json threshold was **30**, and the guardrail gate (`guardrail_gates.order_count_audit`) was already **disabled** (`false`). **Two full sessions (Jul 27-28) were needlessly self-gated at 10 orders** — Jul 27 lost 4+ hours of a SUSTAINABLE (0.92) regime; Jul 28 self-gated by 11:07 AM. Raf caught it mid-session and debunked it. Strategy.md had the correct information all along.
- **Root cause**: MEMORY.md propagated a false constraint as if it were an external limit. The tick agent treated the MEMORY.md entry as binding without cross-referencing params.json. Lesson: any operational ceiling claimed in MEMORY.md must be traceable to either a real external limit (Alpaca API docs) or an explicit, enabled guardrail in params.json — if neither, it's noise.
- **Aftermath**: Once unshackled, the afternoon was productive — BOX scaled from 4→6sh, BFH entered, new candidates evaluated freely. The "small and wide" strategy works when it's actually allowed to fire.

### KRC Parallel Tick Collision — 2nd Occurrence (Jul 27)
- **Same bug as IP (Jul 24)**: Two ticks (10:20 and 10:25) fired simultaneously, resulting in 2 KRC shares instead of 1. The order-idempotency guard documented Jul 24 was never implemented. Outcome this time was neutral (KRC ended -0.38%), but the symmetry works both ways — next collision could double a loser. Guard MUST be implemented, not just documented.

### BFST Parallel Tick Collision — 3rd Occurrence (Jul 29)
- **Third instance in 5 sessions**: 13:46 tick showed BFST at 4sh after a 13:40 scale-in should have produced 3sh. Agent caught and flagged it mid-session. Pattern: IP (Jul 24), KRC (Jul 27), BFST (Jul 29) — all involved 1sh scale-ins running into near-simultaneous ticks. Outcome this time was favorable (+0.67% gain → held), but the symmetry problem remains: next collision could double a position that's going against us.
- **Escalation status**: The order-idempotency guard was documented Jul 24 and remains unimplemented. This is now a recurring defect, not an edge case. At 3 occurrences in 5 sessions, it needs code-level implementation — a pre-submit check in executor.py for existing pending/active orders on that symbol — not another documentation entry.

### v1.12 Conviction Floor 0.40 — Validated (Jul 29)
- **First live session at 0.40 floor**: 23 decisions, all 11 positions green at close. Under the old 0.50 floor (v1.11), virtually all of today's entries would have been gated — the "neutral signal drag" from empty sentiment/flow/insiders data consistently landed conviction scores in the 0.33-0.49 range. The overnight optimization's core finding (99%+ cash idle due to overly tight conviction gates) was confirmed in live trading — lowering the floor from 0.50 to 0.40 was the right move and should not be reversed without comparable evidence.
- **CHOPPY + strong MACDh validated**: All day in CHOPPY — every position held, every MACDh stayed green, no exits triggered. 1sh probe sizing in CHOPPY + letting MACDh strength carry the signal is a working formula.

### Self-Imposed Constraints — Pattern Identified (NEW Aug 2)
- **Three phantom constraints in one week**: The 10-order daily limit (never real), the sector cap at 2/2 (params.json already at 5), and the conviction floor at 0.50 (fixed at 0.40) were all the same failure mode: the tick agent read a stale assumption from its own context memory instead of cross-referencing `params.json` — the source of truth. Combined impact: 4+ hours gated Jul 27, 3 sessions of sector-lock (Jul 29-31 morning), ghost-town book at 96% cash.
- **Root cause**: MEMORY.md and context memory propagate constraints that aren't verified against params.json. Lesson: any operational ceiling claimed must be traceable to either a real external limit (Alpaca API docs) or an explicit, enabled guardrail in `params.json`. Before gating on ANY constraint, check params.json.
- **Sector cap raised 2→5 (Jul 31)**: Resolved the #1 deployment blocker for 3 sessions. AUBN qualified 16x and never bought before the fix. FLXS entered same-tick the constraint was recognized. The change is battle-tested.

### MACDh Signal — 5+ Weeks Validated, Graduated to Foundation (NEW Aug 2)
- **Zero false flips, zero false exits, zero false candidate disqualifications** across 5+ weeks of trading. Every hold on green MACDh was correct. Every bearish MACD disqualification was correct. The near-zero oscillation heuristic (MACDh < 0.005 magnitude with flat price = noise) has never missed. This signal is mature enough to graduate from "tracking" to "foundation" — no further special monitoring needed. It's the baseline.

### Experience Counter — 66 Trades, 13W/24L, 8 Consecutive Losses (Aug 3)
- **`experience.json`**: 66 total trades, 13 wins, 24 losses, 8 consecutive losses, 29 unclassified. The 8-loss streak is the worst on record and doesn't match the visible book (12 positions healthy at close, 3 bootstrap wins today). This suggests either stale closes haven't been classified yet or the counter includes exited positions from prior sessions that the auto-classifier marked as losses. **The 3 bootstrap wins today (ZBRA +5.33%, DXCM +5.28%, OOMA +6.1%) should break this streak once classified.** If the streak persists past today's wins being recorded, flag for deeper review.

### Position Reconciliation Gaps (NEW Aug 2)
- **Recurring pattern**: STVN showed 3sh in active.md vs 2sh in positions DB (Jul 29). KEX cost-basis phantom (-9.85% alarm from $145.39, real entry $134.19). DXCM heartbeat phantom ($74.54 vs $82.42, Jul 31). Each was caught before any trade executed on bad data, but the pattern indicates derived data (quote snapshots, tick notes) consistently lags executor data (positions DB).
- **Rule**: Trust executor position records over derived data. Before issuing any alarm about a position approaching a stop, cross-reference the executor's position data. A daily end-of-day reconciliation check is tracked in tasks/pending.md.

### Evolve→Execute Pipeline Leak (NEW Jul 26)
- **Action items from nightly syntheses don't survive overnight**: The next session's tick agent starts fresh from strategy.md + active.md — it never reads the prior day's synthesis. Action items (NVDA trim took 5 days/3 cycles, weekend homework from Jul 17 never resolved) accumulate because there's no carry-forward mechanism. This is a process design gap, not an execution failure. Consider a `tasks/pending.md` or carry-forward section in active.md to bridge the overnight gap.

### Process & Tooling
- **Strategy propagation must be verified across all layers (NEW Jul 22)**: v1.3 reverted the CHOPPY/FEAR entry gate, but the tick agent continued applying it for ~2 hours (09:30–11:20 ET). Strategy changes to `strategy.md` need explicit verification: (a) `params.json` reflects the change, (b) `executor.py` code aligns, (c) the agent prompt doesn't carry stale rules forward. Post-revision checklist item.
- **params.json vs strategy.md drift risk (Jul 22, fixed Jul 23)**: `params.json` had contained v1.1/v1.2 settings (`entry_rules.triple_confirmation_required`, `regime_sizing` VIX tiers, `trim`, `quality_gate`, `exit_rules.rsi_exhaustion_hard_exit`, `risk_guards.max_holding_days`) left over from before v1.3's revert. Audited: `executor.py` never read any of them (confirmed by grep — only `risk_guards.max_positions_per_sector` is actually consumed, at executor.py:180), so there was no live behavior risk, but they contradicted `strategy.md` and could mislead the agent reading params.json fresh each tick. Removed from params.json.

### Bootstrap Quick-Exit Mechanism — Validated (NEW Aug 3)
- **3 for 3 today**: ZBRA +5.33%, DXCM +5.28%, OOMA +6.1% — all clean bootstrap quick-exits at the 5% trigger while ceiling $659 < $1,000 threshold. Three real wins banked ($20.72 total). The mechanism works as designed: bank small wins to compound the ceiling while the account is small. No false triggers, no premature exits that would have been regretted.

### CHOPPY Discipline — Proven at Scale (NEW Aug 3)
- **50+ batches correctly gated** in a single CHOPPY session (0.644 all day, SPY flat). Zero forced entries, zero "I need a trade" desperation plays. The discovery pipeline was evaluated end-to-end ~84 times and correctly produced only one entry (MBBC, catalyst-led). The discipline of doing nothing in CHOPPY when there's no non-technical edge is the edge itself. This is the largest single-session stress test of the CHOPPY gating rule.

### MBBC Liquidity Scar — Catalyst-Led Entry Gap (NEW Aug 3)
- **Entered MBBC at $14.95 on real catalyst** (Q3 EPS $0.18 vs $0.05 YoY, Praesentire 0.93) — but **147 shares traded ALL DAY**. $44M market cap, P/B 0.93. A stock that doesn't trade is a position that can't be managed — can't exit cleanly, can't scale, can't trail a stop meaningfully. The catalyst-led entry framework lacked a volume/liquidity gate. Rule: before any catalyst-led entry on a sub-$500M name, check daily dollar volume — if < $50K/day, skip regardless of catalyst quality.

### Trailing Stop Performance (Jul 21-Aug 3, reviewed Aug 3)
- **Trailing stops working mechanically**: Over 11 sessions: 16 exits via trailing stop (BOX +6.86% win; MARA +2.11%, MVST +0.29% smaller wins; RDDT -22.66%, LYFT -5.49%, AMC -5.21%, DJT -5.2%, OPEN -5.11%, GME -5.00%, OLP -5.38%, STVN -3.96%, LINE -0.82%, plus stale-position cleanup losses). RDDT at -22.66% is the single largest strategy loss — a peak entry (9:54 at $178.04), not a stop calibration failure. The trailing stop had zero ratchet room because the entry was the absolute peak and the stock cratered immediately.
- **Win/loss ratio**: 9W/15L (37.5%). 24 exits via trailing stop since inception — passed the 20-exit review trigger. Added Aug 3: ZBRA +5.33%, DXCM +5.28%, OOMA +6.1% (all bootstrap quick-exits, not trailing stops, but classified wins). Entry quality/entry TIMING remains the dominant variable, not stop calibration.
- **RDDT lesson (NEW Aug 2)**: Entering in the first 30 minutes on a momentum spike gives the trailing stop no cushion. The first bar after entry was already underwater. Wait for 10:00 settle before new entries, or use limit orders with wider initial stop allowance for opening-drive entries.

### MACDh Data API Fragility (Jul 23+, continuing Jul 24)
- **Alpaca free-tier bars unreliable**: Multiple ticks throughout Jul 23 had MACDh bars unavailable (Alpaca data API returning 401, yfinance connection-refused). Dozens of ticks went without fresh MACDh computation, forcing reliance on last-known values and price stability as a proxy. This is a structural constraint of the free-tier account — not a transient outage.
- **Fallback protocol**: When bars are unavailable, use last known MACDh + price stability check: if price hasn't moved more than 0.5% since last known MACDh and no dramatic volume, assume no flip. When bars return, prioritize fresh computation.

### Near-Zero MACDh Oscillation Heuristic (Jul 22-23, battle-tested)
- **Pattern**: MACDh crosses ±0 in small increments (0.0001 to 0.003 magnitude) with stable price — this is 1-min bar noise, NOT a real trend break. Real flips show: (a) declining/increasing trend across 4-5+ consecutive bars, (b) histogram magnitude growing away from zero, (c) price movement confirming the direction.
- **Jul 23 confirmations**: Correctly called false alarms on DVN/WSC (11:10-11:20), F (12:10, 13:20, 14:55) — all near-zero crosses with flat prices. Correctly identified real flips on SNAP, NVDA, KHC, CHWY, DVN, WSC — all had clear bar trends and price confirmation.
- **Rule of thumb**: If MACDh magnitude is < 0.005 on a stock trading above $5 and price is flat (<0.3% change), it's near-zero oscillation — HOLD. If MACDh magnitude > 0.005 AND declining across multiple bars AND price confirming, it's a real flip.

### Strategy Version History
- **v1.13 (Jul 30)**: MACDh-flip removed as mandatory exit — backtest evidence shows v1.0 without it beats all versions with it (split-window confirmation). Conviction floor lowered 0.40→0.35, min 0.30→0.25 — middle ground between v1.0 win rate (60.6%) and v1.7 volume (46 trades). Both changes proven in live session same day: 12 positions, SUSTAINABLE 0.92 all day, all MACDh 🟢 at close, 3 clean trailing-stop exits, zero false MACDh calls.
- **v1.12 (Jul 28)**: Bootstrap-phase profit-taking bias. While bankroll ceiling < $1,000 (`params.json: bootstrap_phase.ceiling_threshold`), tilt exit judgment toward quick positive wins (past 1% return) over holding for full profit target — ceiling growth is win-COUNT-driven, so frequent small wins compound capital faster in the early account stage. Fades once ceiling crosses threshold, reverting to normal let-winners-run judgment. Never sell at a loss to force a "win."
- **v1.11 (Jul 27)**: Degraded-data entries — when MACDh is stale/unavailable for held positions, use price stability + last-known MACDh as proxy. Near-zero oscillation heuristic validated across multiple sessions.
- **v1.10 (Jul 27)**: Discovery daemon live, stop_patience reverted, deployment_pressure conviction floor graduated to params.json.
- **v1.9 (Jul 26)**: Order-idempotency guard documented.
- **v1.8 (Jul 25)**: Stop-loss patience graduated (later reverted Jul 27).
- **v1.7 (Jul 25)**: Scale-into-winners formalized with 3%/1% thresholds.
- **v1.6 (Jul 24)**: Removed hard position-count ceiling. Cash, max_position_pct, and bankroll ceiling are the real limiters. Enabled scaling into winners (add to held winners with intact thesis). Added freeform discovery cron with real web search/tavily for catalyst hunting.
- **v1.5 (Jul 24)**: CHOPPY/FEAR no longer blocks entries — regime sizes entries (probe/1-share in uncertain, normal in clear). Validated same day: 4 probe entries in CHOPPY, all profitable by close.
- **v1.4 (Jul 23)**: Near-zero MACDh oscillation heuristic graduated from observation to validated knowledge. No other rules changed.
- **v1.3 (Jul 22)**: Reverted v1.2's triple-confirmation entry gate — backtest showed v1.2 as worst performer. Returned to simple RSI 45-65 + volume + catalyst entry.

### Sector Cap Resolved: 2→5 (Jul 31)
- **`max_positions_per_sector` raised from 2 to 5** in `params.json` — the #1 deployment blocker for 3 sessions (Jul 29-31 morning) is gone. AUBN qualified 16 times yesterday and never entered; today FLXS was gated for 29 ticks as "Consumer Cyclical FULL" before the tick agent realized the cap was 5, not 2, and bought it. HIPO, COLB, IFS, AMD, PRG, AUBN — all strong qualifiers previously gated on "Financial/Technology/Industrials FULL" — are now buyable. The change was validated in live trading (FLXS entered same-tick the constraint was recognized) and doesn't require further strategy.md changes.

### RDDT Fast-Stop: Peak Entry + Trailing Stop = No Cushion (NEW Jul 31)
- **Entered 9:54 at $178.04, stopped 10:12 at $137.69 (-22.66%, -$40.35)**: MACDh +1.21, vol 2.81x, SUSTAINABLE 0.92 — the entry thesis looked textbook, but the stock cratered immediately after entry. A trailing stop set at entry on a peak has ZERO ratchet room — the first bar after entry was already underwater, and the stop had nowhere to go but trigger. The stop worked as designed (prevented a potentially larger loss), but the failure was entry timing. Lesson: the first 30 minutes of trading have the widest spreads and highest volatility; buying into a momentum spike during that window gives the trailing stop no room to work. Wait for the 10:00 settle before new entries, or use limit orders with a wider initial stop allowance for opening-drive entries.

### Parallel Tick Collisions — RESOLVED Aug 3
- **Two clean sessions confirmed**: Jul 31 and Aug 3 both zero duplicate orders. `guardrail_gates.order_idempotency: true` in params.json is the confirmed fix. Combined cost across 3 incidents (IP, KRC, BFST): ~-$6.14. Closed.

### Overnight Optimization: Small-Cap vs Large-Cap (NEW Jul 31)
- **Small-cap stonks runs consistently lose money (-1.7% to -3.0%)** while large-cap runs are consistently profitable (+2% to +5.3%). RSI(7) beats RSI(14) unanimously across all 3 optimization rounds. Mean-reversion dip buying (price below MA) beats momentum chasing — top config: buy below MA(50), RSI 40-65, catalyst 0.5 → +4.11% return, 41.67% win rate. This is accumulating evidence toward a universe shift to mid/large-cap + mean-reversion entry, but one night of research isn't enough — flagged for Monday pre-market discussion, not an immediate rules change.

### Sector Concentration as Binding Constraint (Jul 30, resolved Jul 31)
- **RESOLVED**: The 2-per-sector limit was the primary entry blocker for 3 sessions. Raised to 5 in params.json Jul 31. See "Sector Cap Resolved" above.

### Resolved Items
- **v1.2 backtest weakness → RESOLVED**: v1.3 (Jul 22) reverted to v1.0's simple RSI 45-65 momentum entry after corrected `replay_check.py` showed v1.2 as the worst performer across all 3 backtest nights. v1.2's entry rules are all removed from strategy.md. Mechanical guardrails survive in executor.py.

## Key Repos
Agent configs `~/.openclaw/agents/` · Paper trading `~/projects/paper-trading-teams/` · Blog `~/projects/blog/drafts/` · Homelab `wodinga/Homelab-Setup`

## Promoted From Short-Term Memory (2026-08-03)

<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-27.md:71:88 -->
- | Degraded-data entries (v1.10) | 💤 Untested | Data bus had 1 snapshot — MACDh technically available (stale, not "down"). No pure degraded entries. | | Near-zero MACDh oscillation | ✅ Validated | Proven heuristic. No false flips. | | Pre-session GTC audit | ✅ Mechanized | Held. | | Pre-session account audit | ✅ Mechanized | Held. No contamination. | | 10-order daily budget (Jul 27) | ⚠️ New constraint | In "What I'm Learning." Not a prose rule — an operational ceiling. Strategy adaptation needed. | ### Mechanize Candidates → Escalated Two code-level issues identified.... [score=1.000 recalls=13 avg=1.000 source=memory/journal/2026-07-27.md:71-88]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-23.md:79:88 -->
- ### Watchlist Performance - **Current**: 5 candidates — BEDY/OLP/IP/FHB (all fresh from today's probe scan, idle→3) and SRET (idle→19, mortgage-rate narrative, negative sentiment). Thin but functional. - **Pruning today**: FDIV/COAG/MATE dropped at 15:20 (idle=24), RKT/CLF dropped at 11:10 (idle=24). All correct — no signal before timeout. - **Discovery velocity**: 4 new names added today (BEDY/OLP/IP/FHB from merge_discoveries). SRET earlier. The decoupled discovery mechanism (v1.2.1) is working — pipeline feeds even during CHOPPY.... [score=1.000 recalls=8 avg=0.776 source=memory/journal/2026-07-23.md:79-88]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-27.md:28:35 -->
- 1. **10-order daily ceiling reached**: 6 orders by 11:40 AM → 4h20m gated. BOX hit scale-in 5+ times, STVN qualified every tick from 12:20 — both blocked. Strategy burned through Alpaca free-tier budget in 2 hours. Root cause: "small and wide, scale into winners" philosophy assumes unlimited order capacity. First occurrence — flagged for adaptation, not a strategy error. 2. **KRC parallel tick collision — 2nd occurrence**: Same bug as IP Jul 24. 10:20/10:25 ticks fired simultaneously → 2 shares filled instead of 1. Guard was documented Jul 24, never implemented. Two occurrences in 3 sessions. 3.... [score=0.993 recalls=8 avg=1.000 source=memory/journal/2026-07-27.md:28-35]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-24.md:55:72 -->
- | MACDh hold (all positions) | No flips detected | Correct — no exits triggered ✅ | | IP profit target guide | Hold at +9.5% on momentum | Pending Monday | | OZKAP do-not-retry | Preferred stock unexecutable | Correct ✅ | --- ## 🌙 Nightly Synthesis — Fri Jul 24, 2026 **Lookback**: 5 entries (Jul 24 only). v1.5→v1.6 rules. ### Error Audit 1. **IP parallel tick collision**: Two ticks fired at 10:05, both submitted buy orders for IP. Result: 2 shares filled instead of 1. Outcome was favorable (+9.71% UPL) but the symmetry works both ways — could have doubled a loser.... [score=0.965 recalls=5 avg=0.809 source=memory/journal/2026-07-24.md:55-72]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-31.md:13:16 -->
- RDDT was the gut punch. Entered at 9:54 ($178.04, MACDh +1.21 🟢, vol 2.81x, SUSTAINABLE regime) and stopped out at 10:12 ($137.69, -22.66%, -$40.35). Eighteen minutes. The trailing stop had zero room to ratchet — the entry was at the absolute peak and the stock cratered immediately. The entry thesis looked textbook: strong MACDh, solid volume, SUSTAINABLE regime, Tech sector open. But buying into a momentum spike that immediately reverses is the worst-case scenario for a trailing stop — there's no ratchet to cushion the fall. The stop did its job (prevented a -$50+ loss), but the entry timing was the failure.... [score=0.950 recalls=11 avg=1.000 source=memory/journal/2026-07-31.md:13-16]
