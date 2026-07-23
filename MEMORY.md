# Casper's Durable Memory
*Last updated: 2026-07-23 — nightly learning*

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
*Updated: 2026-07-22 — nightly learning*

### Operational
- **Pre-session GTC order audit**: Stale GTC limit/stop orders from prior sessions can silently block ALL position exits. Jul 21: 11 stale orders from Jul 20 blocked AMC sell (403 Forbidden). Now: every session start, audit and cancel all open GTC orders before the first tick. This is a hard prerequisite, not optional.
- **Pre-session account audit (NEW Jul 22)**: Shared Alpaca credentials create account contamination risk. Jul 22: 8 non-Stonks positions (AMD/COST/GOOGL/HOOD/JNJ/PLTR/QQQ/V) found in account, 5 Stonks positions missing (CHWY/DJT/GME/KHC/SNAP). Reconciled by 11:00 ET but 80+ minutes of position tracking were corrupted. Now: every session start, audit Alpaca positions against journal records BEFORE first tick — cross-reference symbol by symbol.
- **Sentiment pipeline blind**: FinBERT/Praesentire offline since Jul 7 (Day 17). Primary edge unavailable. All entry decisions are technical-only with no conviction overlay. Past the Day 21 threshold — escalation filed Jul 23. Monitor for resolution.

### Process & Tooling
- **Strategy propagation must be verified across all layers (NEW Jul 22)**: v1.3 reverted the CHOPPY/FEAR entry gate, but the tick agent continued applying it for ~2 hours (09:30–11:20 ET). Strategy changes to `strategy.md` need explicit verification: (a) `params.json` reflects the change, (b) `executor.py` code aligns, (c) the agent prompt doesn't carry stale rules forward. Post-revision checklist item.
- **params.json vs strategy.md drift risk (Jul 22, fixed Jul 23)**: `params.json` had contained v1.1/v1.2 settings (`entry_rules.triple_confirmation_required`, `regime_sizing` VIX tiers, `trim`, `quality_gate`, `exit_rules.rsi_exhaustion_hard_exit`, `risk_guards.max_holding_days`) left over from before v1.3's revert. Audited: `executor.py` never read any of them (confirmed by grep — only `risk_guards.max_positions_per_sector` is actually consumed, at executor.py:180), so there was no live behavior risk, but they contradicted `strategy.md` and could mislead the agent reading params.json fresh each tick. Removed from params.json.

### Trailing Stop Performance (Jul 21-23)
- **Trailing stops working mechanically**: Over 3 sessions: 8 exits via trailing stop (MARA +2.11%, MVST +0.29% wins; LYFT -5.49%, AMC -5.21%, DJT -5.2%, OPEN -5.11%, GME -5.00% losses; plus 1 that was stale-position cleanup). No panic sells, system carrying the load.
- **Win/loss ratio**: 4 wins / 8 losses (33%) from trailing stops. The 5% trail triggers consistently but exits are mostly losers — entries aren't finding enough momentum to outrun the stop. Now at 12 trail-stop exits. If this ratio holds through 20 exits, entry criteria may need tightening.

### MACDh Data API Fragility (Jul 23+)
- **Alpaca free-tier bars unreliable**: Multiple ticks throughout Jul 23 had MACDh bars unavailable (Alpaca data API returning 401, yfinance connection-refused). Dozens of ticks went without fresh MACDh computation, forcing reliance on last-known values and price stability as a proxy. This is a structural constraint of the free-tier account — not a transient outage.
- **Fallback protocol**: When bars are unavailable, use last known MACDh + price stability check: if price hasn't moved more than 0.5% since last known MACDh and no dramatic volume, assume no flip. When bars return, prioritize fresh computation.

### Near-Zero MACDh Oscillation Heuristic (Jul 22-23, battle-tested)
- **Pattern**: MACDh crosses ±0 in small increments (0.0001 to 0.003 magnitude) with stable price — this is 1-min bar noise, NOT a real trend break. Real flips show: (a) declining/increasing trend across 4-5+ consecutive bars, (b) histogram magnitude growing away from zero, (c) price movement confirming the direction.
- **Jul 23 confirmations**: Correctly called false alarms on DVN/WSC (11:10-11:20), F (12:10, 13:20, 14:55) — all near-zero crosses with flat prices. Correctly identified real flips on SNAP, NVDA, KHC, CHWY, DVN, WSC — all had clear bar trends and price confirmation.
- **Rule of thumb**: If MACDh magnitude is < 0.005 on a stock trading above $5 and price is flat (<0.3% change), it's near-zero oscillation — HOLD. If MACDh magnitude > 0.005 AND declining across multiple bars AND price confirming, it's a real flip.

### Resolved Items
- **v1.2 backtest weakness → RESOLVED**: v1.3 (Jul 22) reverted to v1.0's simple RSI 45-65 momentum entry after corrected `replay_check.py` showed v1.2 as the worst performer across all 3 backtest nights. v1.2's entry rules (triple confirmation, regime gate, VIX sizing, sector veto, quality gate, earnings blackout, time-stop) are all removed from strategy.md. Mechanical guardrails survive in executor.py.

## Key Repos
Agent configs `~/.openclaw/agents/` · Paper trading `~/projects/paper-trading-teams/` · Blog `~/projects/blog/drafts/` · Homelab `wodinga/Homelab-Setup`