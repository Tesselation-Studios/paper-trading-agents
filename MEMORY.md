## Trader-Stonks Durable Lessons
*Updated: 2026-07-29 — nightly learning*

### Operational
- **Pre-session GTC order audit**: Stale GTC limit/stop orders from prior sessions can silently block ALL position exits. Jul 21: 11 stale orders from Jul 20 blocked AMC sell (403 Forbidden). Now: every session start, audit and cancel all open GTC orders before the first tick. This is a hard prerequisite, not optional.
- **Pre-session account audit (NEW Jul 22)**: Shared Alpaca credentials create account contamination risk. Jul 22: 8 non-Stonks positions (AMD/COST/GOOGL/HOOD/JNJ/PLTR/QQQ/V) found in account, 5 Stonks positions missing (CHWY/DJT/GME/KHC/SNAP). Reconciled by 11:00 ET but 80+ minutes of position tracking were corrupted. Now: every session start, audit Alpaca positions against journal records BEFORE first tick — cross-reference symbol by symbol.
- **Sentiment pipeline blind**: FinBERT/Praesentire offline since Jul 7 (Day 22). Primary edge unavailable. All entry decisions are technical-only with no conviction overlay. Past the Day 21 threshold — escalation filed Jul 20, Jul 22, Jul 23; zero response. Now treated as permanent constraint — optimize technical-only workflow, treat sentiment as bonus layer if/when restored.
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

### Experience Counter Partially Fixed (Jul 28 update)
- **Jul 27 gap partially closed**: `experience.json` now shows 40 total trades (was 29). The 11-trade gap from Jul 27 was partially backfilled — but `total_wins` (12) + `total_losses` (16) = 28, leaving 12 trades unclassified. Self-stats pipeline consistently reports "0 trades logged today" in every heartbeat. The `record_decision.py` → self-stats pipeline still has a structural disconnect. Wins/losses tracked manually in experience.json but the automated pipeline doesn't see them.
- **Consecutive losses: 4** — a concerning streak. The last 4 classified closes were all losses. This doesn't match the visible EOD book (all green Jul 27-28), suggesting at least some of these are from closed positions where the P&L was negative. Needs monitoring — if the streak continues into tomorrow, flag for deeper review.

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

### Resolved Items
- **v1.2 backtest weakness → RESOLVED**: v1.3 (Jul 22) reverted to v1.0's simple RSI 45-65 momentum entry after corrected `replay_check.py` showed v1.2 as the worst performer across all 3 backtest nights. v1.2's entry rules are all removed from strategy.md. Mechanical guardrails survive in executor.py.

## Key Repos
Agent configs `~/.openclaw/agents/` · Paper trading `~/projects/paper-trading-teams/` · Blog `~/projects/blog/drafts/` · Homelab `wodinga/Homelab-Setup`

## Promoted From Short-Term Memory (2026-07-29)

<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-21.md:27:45 -->
- **Watchlist starvation**: All 8-10 candidates hit idle_ticks=24 and were pruned at 15:40. Ended day with empty pipeline. - **Sentiment blind Day 14**: Still no FinBERT/Praesentire. Flying without primary edge for 2 weeks. Every entry decision is binary technical-only. - **OPEN persistent weakness**: -3% to -4.5% every tick, always hovering just above $4.275 trail. Survived but precarious. - **v1.2 backtest weakness** (off-hours): Two nights running, replay_check.py shows v1.2 underperforming v1.0 on 200d/22-ticker backtest. Caveats: no sector/VIX/fundamentals modeled. Monitoring, not acting yet.... [score=0.968 recalls=17 avg=1.000 source=memory/journal/2026-07-21.md:27-45]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-22.md:109:119 -->
- 2. **Hardened rule — Strategy deployment checklist**: "A strategy version bump must sync across all layers: (a) strategy.md updated, (b) params.json strategy_version + any param changes, (c) executor.py guardrails checked for conflicts, (d) tick agent system prompt verified against current strategy." Jul 22's 2-hour CHOPPY gate propagation delay proved single-layer changes are incomplete. One incident, but the blast radius (entries silently gated under wrong rules) warrants immediate hardening. 3. **Monitoring flag — Trailing stop win rate**: 2W/4L (33%) from trailing stops across Jul 21-22.... [score=0.848 recalls=6 avg=0.779 source=memory/journal/2026-07-22.md:109-119]
