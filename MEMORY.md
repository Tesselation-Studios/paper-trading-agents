# Casper's Durable Memory
*Last updated: 2026-07-12 — self-review trim*

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
- **Sentiment pipeline blind**: FinBERT/Praesentire offline since Jul 7 (Day 15). Primary edge unavailable. All entry decisions are technical-only with no conviction overlay. If this persists past Day 21, treat it as a structural risk requiring operator escalation.

### Process & Tooling
- **Strategy propagation must be verified across all layers (NEW Jul 22)**: v1.3 reverted the CHOPPY/FEAR entry gate, but the tick agent continued applying it for ~2 hours (09:30–11:20 ET). Strategy changes to `strategy.md` need explicit verification: (a) `params.json` reflects the change, (b) `executor.py` code aligns, (c) the agent prompt doesn't carry stale rules forward. Post-revision checklist item.
- **params.json vs strategy.md drift risk (Jul 22, fixed Jul 23)**: `params.json` had contained v1.1/v1.2 settings (`entry_rules.triple_confirmation_required`, `regime_sizing` VIX tiers, `trim`, `quality_gate`, `exit_rules.rsi_exhaustion_hard_exit`, `risk_guards.max_holding_days`) left over from before v1.3's revert. Audited: `executor.py` never read any of them (confirmed by grep — only `risk_guards.max_positions_per_sector` is actually consumed, at executor.py:180), so there was no live behavior risk, but they contradicted `strategy.md` and could mislead the agent reading params.json fresh each tick. Removed from params.json.

### Trailing Stop Performance (Jul 21-22)
- **Trailing stops working mechanically**: Over 2 sessions: 5 exits via trailing stop (MARA +2.11% win, LYFT -5.49%, AMC -5.21%, DJT -5.2%, MVST +0.29%). No panic sells, system carrying the load.
- **Win/loss ratio concern**: 2 wins / 6 losses from trailing stops. The 5% trail triggers consistently but exits are mostly losers — entries aren't finding enough momentum to outrun the stop. If this ratio holds for 20+ exits, entry criteria may need tightening.

### Resolved Items
- **v1.2 backtest weakness → RESOLVED**: v1.3 (Jul 22) reverted to v1.0's simple RSI 45-65 momentum entry after corrected `replay_check.py` showed v1.2 as the worst performer across all 3 backtest nights. v1.2's entry rules (triple confirmation, regime gate, VIX sizing, sector veto, quality gate, earnings blackout, time-stop) are all removed from strategy.md. Mechanical guardrails survive in executor.py.

## Key Repos
Agent configs `~/.openclaw/agents/` · Paper trading `~/projects/paper-trading-teams/` · Blog `~/projects/blog/drafts/` · Homelab `wodinga/Homelab-Setup`