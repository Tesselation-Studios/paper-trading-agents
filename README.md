# Paper Trading Agents

Agent prompts, strategies, personalities, and self-evolution state for the three AI traders.

**This repo contains:** Strategy files, prompt templates, and reference docs that define how each trader thinks and acts.

**Related repo:** [paper-trading-rebuild](https://github.com/Tesselation-Studios/paper-trading-rebuild) — the engine code: data bus, signal engine, replay harness, dashboard, CI/CD, and nightly optimization pipeline.

### How the Two Repos Fit Together

| Repo | Contains | Deployed to |
|------|----------|-------------|
| **paper-trading-agents** (this one) | Strategy files (`stonks/strategy.md`, `aldridge/strategy.md`, `kairos/strategy.md`), prompt templates, reference docs | OpenClaw VM workspace (`/home/openclaw/.openclaw/`) |
| **paper-trading-rebuild** | Code: data bus, signal engine, replay, dashboard, tests, CI/CD | Docker containers on `.179` (hermes-server) |

### Agent Prompt Files (on OpenClaw VM)

The actual `prompt.txt` files that the agents read are deployed on the OpenClaw VM at:
- `/home/openclaw/.openclaw/agents/trader-stonks/prompt.txt`
- `/home/openclaw/.openclaw/agents/trader-aldridge/prompt.txt`
- `/home/openclaw/.openclaw/agents/trader-kairos/prompt.txt`

These are deployed via `scp` from the rebuild repo. The strategy files in this repo are the source of truth for the strategy content referenced in those prompts.

### Strategy Files

- `stonks/strategy.md` — Community-driven momentum trading
- `aldridge/strategy.md` — Value investing with fundamentals
- `kairos/strategy.md` — HMM regime-filtered momentum
- `strategies/active.md` — Active strategy configuration
- `strategies/params.json` — Signal parameter settings
- `strategies/watchlist.json` — Current watchlist
- `reference/agent-prompt-system.md` — Prompt system reference

### Key Rule

**Agent prompt files must be kept under 2,000-3,000 chars.** Every byte costs tokens on every tick. Bloated prompts cause timeout loops. See the AGENTS.md in the rebuild repo for full size limits.