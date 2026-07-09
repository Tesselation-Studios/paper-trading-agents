# Trading Agent Prompts

> **Canonical repo for all trader agent files.** Consolidation of `paper-trading-prompts` + `paper-trading-agents`.
> Part of the `Tesselation-Studios/paper-trading-rebuild` ecosystem.

---

## Structure

```
traders/
├── kairos/       — Momentum + ML trader (Zara Chen)
├── aldridge/     — Value + fundamentals trader (Edmund Whitfield)
└── stonks/       — Social/sentiment trader (Stan "the Man" Hoolihan)

Each trader has:
  AGENTS.md       — Core strategy, behavior rules, tick workflow
  HEARTBEAT.md    — Daily reflection log (three-step process)
  SOUL.md         — Persona, identity, narrative voice
  IDENTITY.md     — Metadata, brand, relationship to other traders
  TOOLS.md        — Available tools and CLI references
  MEMORY.md       — Persistent learnings, market observations
  prompt.txt      — Pre-assembled trading prompt template
  config.yaml     — Signal engine parameters and thresholds
  skills/         — Reusable skill files (reflection, strategy, etc.)
```

## Repos

| Repo | Purpose |
|------|---------|
| `paper-trading-rebuild` | System architecture, learning loop, COMPETITION.md |
| **`paper-trading-agents`** ← you are here | Trader agent files (AGENTS.md, prompts, HEARTBEAT.md, skills) |

## Branch Strategy

- `main` — production prompts for real traders
- `branches/<trader>/experiment-*` — virtual trader variants
- `branches/<trader>/sweep-YYYY-MM-DD` — nightly sweep results

See `paper-trading-rebuild/COMPETITION.md §C2.6` for full branching details.
