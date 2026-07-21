# Paper Trading Agents

**Consolidated repo** — agent prompts, strategies, personalities, and heartbeat files for the paper trading competition.

Three LLM-powered traders (Kairos, Aldridge, Stonks) running $10K paper portfolios on a distributed homelab. Each trader has their own directory with system prompts, tools, identity, and strategy configs.

## Repo Structure

```
├── traders/
│   ├── kairos/       # Kairós Capital — Zara Chen (momentum)
│   ├── aldridge/     # Aldridge & Partners — Edmund Whitfield (value)
│   └── stonks/       # Stonks Capital — Stan Hoolihan (aggressive)
├── strategies/       # Shared strategy reference
├── state/            # Runtime state directory
├── prompts/          # Shared prompt templates
└── AGENTS.md         # Agent onboarding instructions
```

## Per-Trader Files

| File | Purpose |
|------|---------|
| `AGENTS.md` | System prompt — agent identity, rules, constraints |
| `SOUL.md` | Personality, voice, backstory |
| `IDENTITY.md` | Identity card — name, role, style |
| `HEARTBEAT.md` | Tick checklist — updated each heartbeat cycle |
| `MEMORY.md` | Persistent memory — trading lessons, observations |
| `TOOLS.md` | Tool reference — data bus endpoints, skill commands |
| `prompt.txt` | Evolved prompt — built by nightly sweeps |
| `prompt-changelog.md` | Changelog of prompt evolution |
| `config.yaml` | HMM regime config, signal parameters |
| `daily_tick.md` | Pre-tick context — market state, portfolio snapshot |
| `skills/` | Skill files loaded on demand |

## Lifecycle

- **Heartbeat**: Every 5 min during market hours (Mon-Fri 9:30-16:00 ET)
- **Nightly evolution**: Auto-promote prompts based on trade outcomes
- **Learning loop**: Grade trades, analyze patterns, optimize params (EOD)
- **Sync**: Journal entries and decisions synced to Postgres every 5 min

## Live System

Traders run as OpenClaw agents on the homelab gateway (`192.168.1.41`).
The dashboard lives at `192.168.1.179:5002` and queries Postgres + live Alpaca data.

## Archived Repos

This repo consolidates content previously scattered across:
- `Tesselation-Studios/paper-trading-prompts` — archived
- `Tesselation-Studios/trading-agent-prompts` — archived