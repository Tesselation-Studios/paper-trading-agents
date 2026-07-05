# Paper Trading Agents

Agent personas, strategy configurations, and heartbeat routines for the paper trading competition.

## Traders

| Trader | Firm | Strategy | Model |
|--------|------|----------|-------|
| Zara Chen | Kairos Capital | HMM regime-filtered momentum | deepseek-v4-flash → deepseek-v4-pro |
| Edmund Whitfield | Aldridge & Partners | Fundamental value | deepseek-v4-flash → deepseek-v4-pro |
| Stan Hoolihan | Stonks Capital | Community-driven momentum | deepseek-v4-flash → deepseek-v4-pro |

## Structure

```
traders/
  kairos/         — Zara Chen, momentum trader
    AGENTS.md      — Personality, decision framework, data bus commands
    HEARTBEAT.md   — Heartbeat cadence and daily routine
    SOUL.md        — Core identity and strategy rules
    config.yaml    — Strategy parameters (thresholds, weights, limits)
  aldridge/        — Edmund Whitfield, value investor
    AGENTS.md
    HEARTBEAT.md
    SOUL.md
    config.yaml
  stonks/          — Stan Hoolihan, community momentum
    AGENTS.md
    HEARTBEAT.md
    SOUL.md
    config.yaml
```

## Engine Integration

The paper-trading-teams engine reads trader configurations from this repo.
See `paper-trading-teams/src/agents_repo.py` for the integration layer.

Trader heartbeat code loads AGENTS.md, HEARTBEAT.md, and SOUL.md as startup
context. Strategy parameters in config.yaml feed into risk gates, position
sizing, and conviction thresholds.
