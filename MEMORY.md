# Casper's Durable Memory
*Last updated: 2026-07-26 — weekly review*

## Raf's Preferences
- **Directness**: Follow commands — no scope creep.
- **Heartbeat**: Cron stays DISABLED unless Raf re-enables.
- **"Agents" = persistent OpenClaw agents** (trader-*), not cron jobs. NEVER disable traders without asking.
- **Code delegation** → coder subagent. **Plan-first** → canvas before complex builds.
- **OpenRouter exclusive** for all models.
- **Blog**: Gonzo writes in wodinga/casper-blog. Every post opens with "Casper's Dreams".

## Agent Roster
| Agent | Role | Persona |
|-------|------|---------|
| Casper | Coordinator | Spike + Ed |
| homelab-wizard (Jet) | Infra/config steward | Grizzled sysadmin |
| coder | Code, PRs, tests | Proposes config |
| researcher | Web/papers/synthesis | Cites sources |
| orchestrator | Planning/facilitation | Planner |
| gonzo | Blog writer, log miner | Thompson-esque |
| alt | Raf's partner | af_nicole |
| trader-aldridge | Value investing | Edmund Whitfield |
| trader-kairos | Momentum | Zara Chen |
| trader-stonks | Aggressive | Stan Hoolihan |

## Routing
**Jet**: infra, Docker, SSH, Traefik, config. **Coder**: code, PRs, deployments. **Researcher**: web, papers, deep synthesis. **Orchestrator**: multi-domain projects. **Gonzo**: blog + log mining via sessions_history.

**Governance**: Orchestrator = facilitator. Config changes via Jet committed to git. Budget check before paid work. Depth 1 only — no peer-to-peer spawning outside Casper/Orchestrator.

## Model Strategy
**Casper**: `openrouter/deepseek/deepseek-v4-flash` (pro via `/model`). **Jet/Alt/Coder/Researcher/Orchestrator/Gonzo**: deepseek-v4-pro. **Traders**: deepseek-v4-flash. Fallback: flash → minimax-m3 → qwen3.7-plus → gemini-3.5-flash → free → auto. Context: 1M tokens.

## Config Lessons
- `subagents.allowAgents` (not `allowedAgents`). `tools.agentToAgent.allow` (not `allowAgents`).
- `contextTokens` top-level only, NOT nested model-level.
- **active-memory plugin**: BROKEN (upstream bugs). Keep disabled.
- Stale auth keys in sqlite `auth_profile_store` override openclaw.json.
- Batch all config changes → one restart. Multiple rapid restarts corrupt.

## Incidents
🔴 docker.klo: direct ports closed (Jun 14), Traefik-only.
🟡 wadinga.studio DNS expired (Jun 12), use IPs.

## Notification Routing
- Trader health warnings → canvas only. Log to `logs/health.log`.
- Gateway health → NEVER Telegram unless RSS>2GB or disk<500MB.
- Critical alerts → Telegram + canvas.
- Telegram = conversation + critical alerts ONLY.

## Trader-Stonks Durable Lessons
*Updated: 2026-07-24 — nightly learning*

### Operational
- **Pre-session GTC order audit**: Stale GTC limit/stop orders from prior sessions can silently block ALL position exits. Jul 21: 11 stale orders from Jul 20 blocked AMC sell (403 Forbidden). Now: every session start, audit and cancel all open GTC orders before the first tick. This is a hard prerequisite, not optional.
- **Pre-session account audit (NEW Jul 22)**: Shared Alpaca credentials create account contamination risk. Jul 22: 8 non-Stonks positions (AMD/COST/GOOGL/HOOD/JNJ/PLTR/QQQ/V) found in account, 5 Stonks positions missing (CHWY/DJT/GME/KHC/SNAP). Reconciled by 11:00 ET but 80+ minutes of position tracking were corrupted. Now: every session start, audit Alpaca positions against journal records BEFORE first tick — cross-reference symbol by symbol.
- **Sentiment pipeline blind**: FinBERT/Praesentire offline since Jul 7 (Day 19). Primary edge unavailable. All entry decisions are technical-only with no conviction overlay. Past the Day 21 threshold — escalation filed Jul 20, Jul 22, Jul 23; zero response. Now treated as permanent constraint — optimize technical-only workflow, treat sentiment as bonus layer if/when restored.
- **Parallel tick collision guard (NEW Jul 24)**: Two ticks firing simultaneously can submit duplicate buy orders for the same ticker. Jul 24: IP got 2 shares instead of 1 due to parallel tick collision at 10:05 — outcome was favorable (+9.7%) but the symmetry works both ways. Before submitting a buy order, check for existing active/pending orders on that symbol.
- **Preferred stock screening (NEW Jul 24)**: Alpaca paper trading does NOT support OTC preferred stocks. Jul 24: OZKAP buy order submitted @ $16.40, never filled — order appeared in recent_orders but position absent from account. Screen ticker type (common/ETF/preferred/OTC) at watchlist qualification stage, not execution stage. Flag OTC/preferred as "do-not-retry."

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

## Promoted From Short-Term Memory (2026-07-27)

<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-24.md:88:103 -->
- 1. **Add order-idempotency guard**: Before submitting a buy order, check for existing pending orders on the same ticker. A simple "is there already an active order for this symbol?" gate would prevent parallel-tick double-fills. 2. **Pre-screen ticker types at watchlist stage**: OZKAP was a preferred stock — should have been caught before reaching the executor. Add a ticker-type check (common/ETF/preferred/OTC) to watchlist qualification. 3. **Monday F earnings prep is critical**: F reports Q2 Tuesday 7/28. Pre-market Monday needs a thesis review and decision on hold-through or exit-before. ### Params - No param changes needed.... [score=0.741 recalls=4 avg=0.559 source=memory/journal/2026-07-24.md:88-103]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-24.md:99:109 -->
- **F earnings Tuesday 7/28**: Pre-earnings thesis review required Monday. ### Action Items (Mon Jul 27) - 🔔 **F Q2 earnings prep**: Review thesis. Hold-through or exit-before decision needed. - 📊 **IP profit target**: At +9.71%, check momentum. If MACDh stays bullish, hold. If stalling, consider exit at/before 12%. - 🔍 **Evaluate BKSY, RCAT**: Get price data, RSI, volume, catalyst for the 2 new watchlist candidates. - 🛠️ **Order idempotency guard**: Investigate feasibility of pre-submit check for active orders on same ticker. - 🧹 **VTEX**: At idle_ticks=20. If volume stays 0.45x, it hits threshold Monday.... [score=0.741 recalls=4 avg=0.559 source=memory/journal/2026-07-24.md:99-109]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-17.md:30:60 -->
- **Biggest worry**: FUBO -4.98% day. That's a flush. MACDh still +0.10 so we're not at flip yet, but Monday open is the test. LYFT MACDh at +0.04 — thinner than a dime. Both on watch. **Best looking**: NVDA divergence still firing. Close at $202.80 above mid-BB ($202.05). RSI 49.4 — that's textbook divergence territory. If we get a tech bounce Monday, NVDA could rip to $208+. **Surprise performer**: F is now TRENDING_UP regime. 1 share, but it's a signal. Ford catching a bid in this mess? Consumer staples+auto rotation narrative. **Oversold watch**: QQQ RSI 44.7. If we get sub-40 Monday, that's a buy-the-dip setup.... [score=0.678 recalls=3 avg=0.571 source=memory/journal/2026-07-17.md:30-60]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-23.md:42:64 -->
- F Q2 earnings Monday 7/28. Only position left. Flagged. ## Musings The watchlist is thin — 5 names, 4 of them fresh from today's probe scan with no real thesis yet. SRET has a mortgage-rate narrative but negative sentiment and 19 idle ticks. If regime clears to BULLISH next week, I need ready names. Discovery must keep running even through CHOPPY. Also chewing on: what's the game plan when everything exits? I ended today with 1 position. If F exits on Monday pre-earnings review, I'm at zero. Starting from scratch with a $10,400 bankroll isn't the worst problem, but the pipeline needs to be primed.... [score=0.656 recalls=3 avg=0.559 source=memory/journal/2026-07-23.md:42-64]
<!-- openclaw-memory-promotion:memory:memory/journal/2026-07-23.md:127:139 -->
- **Data API fragility**: Alpaca free-tier 401s and yfinance refusals increasing in frequency. Structural constraint. Not actionable from here, but tracked. ### Action Items (Fri Jul 24) - 🔔 **F Q2 earnings prep**: Earnings due Mon 7/28. Review thesis Monday pre-market. Only position left. - 🔍 **Prime the watchlist**: Research RSI/volume/catalyst for all 5 candidates (BEDY/OLP/IP/FHB/SRET) so they're ready-to-fire if regime clears. - 📝 **MACDh fallback exploration**: Investigate whether MACDh can be computed from quote snapshots as a lightweight fallback when bar data is unavailable.... [score=0.635 recalls=3 avg=0.532 source=memory/journal/2026-07-23.md:127-139]
