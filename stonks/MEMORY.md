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

## Key Repos
Agent configs `~/.openclaw/agents/` · Paper trading `~/projects/paper-trading-teams/` · Blog `~/projects/blog/drafts/` · Homelab `wodinga/Homelab-Setup`