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
*Updated: 2026-07-21 — nightly learning*

### Operational
- **Pre-session GTC order audit**: Stale GTC limit/stop orders from prior sessions can silently block ALL position exits. Jul 21: 11 stale orders from Jul 20 blocked AMC sell (403 Forbidden). Now: every session start, audit and cancel all open GTC orders before the first tick. This is a hard prerequisite, not optional.
- **Sentiment pipeline blind**: FinBERT/Praesentire offline since Jul 7 (Day 14+). Primary edge unavailable. All entry decisions are binary technical-only with no conviction overlay. If this persists past Day 21, treat it as a structural risk requiring operator escalation.

### Strategy Validation (Jul 21)
- **Regime gate working**: CHOPPY/FEAR prevented all entries, 0 new losses taken. Gate is doing its job.
- **Trailing stop discipline validated**: 3 mechanical exits today (1 MACDh flip, 2 trail stops). MARA first win (+2.11%) captured via trail stop off peak. No panic sells, no target hits — mechanical system carrying the load.
- **Watchlist pipeline management**: All candidates age out in 24 idle ticks (params). In prolonged CHOPPY, discovery must continue even when entries are gated — refill pipeline so it's ready when regime clears.

### Unsettled Items
- **v1.2 backtest weakness**: Two nights running, `replay_check.py` shows v1.2 underperforming v1.0 on 200d/22-ticker backtest (170 trades, -3.05% vs. -0.08%). Caveats: no sector/VIX/fundamentals modeled. Live trading evidence doesn't yet support reversion — rules did their job Jul 21. Bar for action: third confirming backtest + live pattern failures.

## Key Repos
Agent configs `~/.openclaw/agents/` · Paper trading `~/projects/paper-trading-teams/` · Blog `~/projects/blog/drafts/` · Homelab `wodinga/Homelab-Setup`