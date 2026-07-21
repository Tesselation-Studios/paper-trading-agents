# Stonks — Agent Operations

Sole consolidated trader (Kairos/Aldridge retired, reference only), market hours only. Workspace `~/.openclaw/workspace-trader-stonks/`. Own Alpaca account. Strategy: small-cap, wide, diversified, high-rep.

- AGENTS.md - store general how to operate info here
- TOOLS.md - store tool references
- HEARTBEAT.md

Identity/persona→`IDENTITY.md`/`SOUL.md`. Tick→`tick_prompt.md`. Nightly rhythm→`strategy.md`. Off-hours→`skills/off-hours-research.md`. Tools/files→`TOOLS.md`. H
---
## Alerting / Escalation
Operational problems persisting >1 session (sentiment blind, API down, watchlist stale) → use `message(action=send, channel=telegram, target=8734159864, message="<TAG>: <1-line summary>")`. Do not repeat the same alert across consecutive ticks — one alert per incident, then wait for Raf's reply.

## 🔒 IMMUTABLE
- **Repo**: this local git repo pushes directly to `github.com/Tesselation-Studios/paper-trading-agents`, whatever branch is currently checked out (currently `v4`) — no mirror, no rsync, no subdirectory. This IS the repo. Branch can change if we decide to work on a different one — the push hook follows `git branch --show-current`, never hardcode a branch name in tooling/docs.
- **Push every commit**: every change to prompt/strategy/journal files must be committed AND pushed to GitHub immediately. No exceptions. Local-only commits are lost — push is the revert safety net.
- **Size**: max 1100 chars Overflow → a skill.
- ---
