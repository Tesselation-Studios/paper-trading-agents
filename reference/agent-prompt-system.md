# Agent Prompt System — Reference for Traders

> **Repo**: `github.com/casper-bot-wodinga/paper-trading-agents`
> **Branch**: `main`
> **Purpose**: Version-controlled prompts, strategies, and self-evolution state

---

## 1. Repo Structure

```
paper-trading-agents/
├── README.md           # Overview
├── kairos/             # Kairos prompts + strategy
│   ├── AGENTS.md       # Core prompt (loaded every tick)
│   ├── SOUL.md         # Persona
│   ├── TOOLS.md        # Tool usage reference
│   ├── HEARTBEAT.md    # Tick checklist
│   └── strategy.md     # Current strategy config
├── aldridge/           # Aldridge prompts + strategy
│   └── strategy.md
├── stonks/             # Stonks prompts + strategy
│   └── strategy.md
├── strategies/         # Shared strategy definitions
│   ├── README.md       # How strategies work
│   ├── active.md       # Current active strategy (human-readable)
│   └── params.json     # Current SignalParams (machine-readable)
├── prompts/            # Shared prompt templates
└── state/              # Self-evolution state
```

---

## 2. How to Read Strategies

**Traders**: At startup, read `strategies/params.json` for current parameter values:

```json
{
  "params": {
    "momentum_threshold": 0.55,
    "base_size_pct": 0.15,
    "stop_loss_pct": 0.05,
    ...
  }
}
```

These feed into `SignalEngine.process(tick)`, which returns a `SignalReport` with:
- `momentum_score`, `rsi`, `volatility` — indicator values
- `momentum_signal`, `rsi_signal` — BULLISH/BEARISH/NEUTRAL/OVERSOLD/OVERBOUGHT
- `regime` — TRENDING_UP/DOWN/MEAN_REVERTING/HIGH_VOLATILITY
- `composite_signal` — blended signal (-1 to 1)
- `conviction` — engine confidence (0 to 1)
- `stop_loss`, `take_profit` — risk prices

---

## 3. Flipping Through Strategies

The nightly sweep tests 8 variants and picks the best one. The winner updates `strategies/params.json`. If you want to manually flip:

1. Read `strategies/active.md` to understand the current strategy
2. Check `strategies/params.json` for exact values
3. To try a different approach, modify params.json with `hermes tools` or direct commit

**Strategy templates** (from nightly_replay.py):
- `wider_stops`: +50% SL/TP
- `tighter_stops`: -30% SL/TP
- `aggressive_sizing`: +40% position size
- `conservative_sizing`: -40% position size
- `momentum_focus`: Heavy momentum, light mean-reversion
- `mean_reversion_focus`: Heavy RSI, light momentum
- `trend_following`: Long lookback, strong trend bias
- `volatility_adaptive`: Tighten in high vol, loosen in low vol

---

## 4. Journaling — Git Commit Tracking

Every journal entry MUST include the current git commit hash of the `paper-trading-agents` repo. This is how we correlate strategy version with performance.

```markdown
## Journal — 2026-07-13 14:30 ET

**Strategy commit**: `git rev-parse HEAD` → abc123def456
**Strategies/params.json**: momentum_threshold=0.55, base_size_pct=0.15, ...
**Trades today**: 3 (2 win, 1 loss)
**Bankroll**: $38.50 (+$3.50, +10%)
```

To get the commit:
```bash
cd /path/to/paper-trading-agents && git rev-parse HEAD
```

**Why this matters**: When we backtest and find that strategy X outperformed on July 13, we can `git checkout abc123def456` to see exactly what params were active. Without this, performance data is disconnected from strategy version.

---

## 5. How Optimization Works

```
     ┌──────────────────────────────────────────────┐
     │                                              │
     │  params.json ──► trader reads ──► trades     │
     │      ▲                            │          │
     │      │                            ▼          │
     │  best variant ◄── leaderboard ◄── replay     │
     │                                              │
     └──────────────────────────────────────────────┘
```

- **Intraday**: Gradient descent perturbs one parameter, re-replays last N ticks, steps toward better score
- **Nightly**: 8 variants tested on yesterday's data, winner updates params.json
- **Commit tracking**: Every params.json update is a git commit, creating a history of strategy evolution

---

## 6. Quick Reference

```bash
# Current strategy
cat /path/to/paper-trading-agents/strategies/active.md

# Current params
cat /path/to/paper-trading-agents/strategies/params.json

# Current commit (LOG THIS IN YOUR JOURNAL)
cd /path/to/paper-trading-agents && git rev-parse HEAD

# Git log of strategy changes
cd /path/to/paper-trading-agents && git log --oneline -- strategies/

# Run nightly replay
cd /path/to/paper-trading-rebuild && python3 src/nightly_replay.py --date 2026-07-13 --dry-run
```