# Paper Trading Agents — v4 System Specification

> **Date**: 2026-07-17
> **Status**: Live (Phase 1 deployed, Aldridge trading)
> **Repo**: `Tesselation-Studios/paper-trading-agents` (main branch)
> **Branch pattern**: `v4/feature-x`

---

## 1. Core Architecture

### Principle
Each trader runs in a **persistent session** during market hours. Context builds up naturally across ticks. No file re-reading for stable files (strategy.md, params.json, AGENTS.md). Only working memory (active.md) and portfolio data get re-read each tick.

### File-Based Memory (not session memory)
Files are the persistence layer. Session context is ephemeral — it lives for the trading day and resets overnight. The files survive restarts, crashes, and context limits.

### Cold Start Is a Feature
The nightly maintenance fires in a **fresh isolated session** with no context from the day's ticks. It reads the day's files fresh and reconstructs the narrative from journal entries + active.md. This forces disciplined writing: if the files aren't good enough, the reflection suffers.

### Git History Is the Safety Net
Every file change is committed immediately via the auto-commit skill. No approval needed for edits. Rollback is `git revert`. The commit trail tracks every strategy evolution, every param change, every thesis update.

---

## 2. File Structure (per trader)

```
aldridge/
├── AGENTS.md              — Identity/persona. Loaded once at session start.
├── SOUL.md                — Edmund Whitfield persona (value trader)
├── TOOLS.md               — Tool references (data bus, executor, journal)
├── HEARTBEAT.md           — Tick checklist + nightly maintenance instructions
├── tick_prompt.md         — TRADING LOOP. Read on every tick. This is where the loop lives.
├── strategy.md            — CONSTITUTION. Fixed beliefs, rules, versioned. Read once, stays warm.
├── params.json            — PARAMETERS. All numerical/toggle params. Read once, stays warm.
├── strategies/
│   └── active.md          — WORKING MEMORY. Rewritten every tick. Carries state between ticks.
├── positions/
│   └── *.md               — Thesis files for open positions. Per-ticker.
├── journal/
│   └── YYYY-MM-DD.md      — DIARY. Concise EOD entry only. Not per-tick.
├── scripts/
│   ├── executor.py        — Alpaca order executor (BUY/SELL/status)
│   └── nightly-maintenance.sh — Legacy bash script (being replaced by cron)
└── skills/
    └── auto-commit.md     — Auto-commit skill: commit any file change immediately
```

### File Roles

| File | Purpose | How Often Read | How Often Written |
|------|---------|---------------|-------------------|
| `tick_prompt.md` | The trading loop instructions | Every tick | Rarely (strategy evolves) |
| `strategy.md` | Constitution (beliefs, rules, versioned) | Once per session (warm) | Nightly (evolves) |
| `params.json` | All numerical params | Once per session (warm) | Nightly (evolves) |
| `strategies/active.md` | Working memory — where was I? | Every tick | Every tick (trim format) |
| `journal/YYYY-MM-DD.md` | Concise diary | Nightly (synthesis step) | EOD only |
| `positions/*.md` | Thesis for each open position | When near trigger distance | On trade/trigger |
| `AGENTS.md` | Identity + nightly instructions | Once per session | Rarely |

---

## 3. Tick Loop (5-min intervals during market hours)

### Flow

```
cron (every 5 min, Mon-Fri 9:30-16:00 ET)
  └─ isolated dispatch session
       └─ sessions_send → each trader's persistent session
            └─ "Market tick — read tick_prompt.md and follow the instructions"
                 └─ tick_prompt.md → read → execute loop
```

### tick_prompt.md Core Loop

1. **Read active.md** — working memory from last tick. Know last state, regime, positions near triggers.
2. **Check portfolio** — `executor.py --status` (prices moved, stops shifted, P&L changed).
3. **Market snapshot** — data bus quotes + macro + regime.
4. **Scan positions near triggers** — stop breaches, profit targets, RSI oversold, concentration limits.
5. **Decide** — BUY/SELL/HOLD with structured JSON. Tight rationale.
6. **Execute** — via Alpaca executor if trade. Update thesis file if position changed.
7. **Update active.md** — append tick entry. Trim format (3-5 lines if no trade/trigger). Full P&L tables only when something changed.
8. **Git commit** — auto-commit any file changes via `skills/auto-commit.md`.
9. **HEARTBEAT_OK**

### Trim Rules
- No re-reading strategy.md or params.json — already warm in session context.
- No P&L tables in the journal. That goes in active.md only.
- If this tick is identical to last tick: write "Same as last tick" and done.
- Journal entry at EOD only, not per tick.

### Executor Script (`scripts/executor.py`)
- Direct Alpaca paper trading API (no MCP, no PG for Phase 1).
- Commands: `--account aldridge --action status`, `--action BUY --ticker KO --qty 1`, `--action SELL`.
- Credentials hardcoded per trader.

---

## 4. Three-Step Nightly Rhythm (16:30 ET, Mon-Fri)

The nightly maintenance fires in an isolated session with a 30-minute budget. It runs three steps:

### Step 1: Journal (10 min)
Write to `journal/YYYY-MM-DD.md`. A **diary entry**, not a dashboard:
- Big picture since last entry (not tick-by-tick)
- How the trader feels — honest, personal
- Portfolio in 2-3 lines
- Musings: how are other traders doing? what do I wish was different? ideas I'm chewing on
- **Keep under 20 lines**

### Step 2: Synthesize (10 min)
Read the last N journal entries (`params.json > synthesis > lookback_n_entries`, default 10) + current strategy.md + today's active.md. Extract signal:
- Errors made (be specific: "bought META at RSI 62 in low-conviction CHOPPY")
- Patterns noticed across N entries (what keeps recurring?)
- Things of genuine interest
- What would I do differently?

### Step 3: Evolve (10 min — highest bar, only if genuinely useful)
- Update `strategy.md` if philosophy or rules changed (bump version: `ald.strat:v1.0 → v1.1`)
- Update `params.json` if numerical params need adjusting
- Create action items: "I will do X next time I see Y"
- Tool requests: "this stop limit should be code, not markdown"
- New techniques to try
- **If nothing evolved: write "Nothing changed this cycle" and done.**
- Git commit all changes with rationale.

---

## 5. Params.json Schema

```json
{
  "agent": "aldridge",
  "account": "aldridge",
  "phase": 1,
  "strategy_version": "ald.strat:v1.1",   // bumped by nightly on evolution

  "tick": {
    "interval_seconds": 300,
    "active_hours": "09:30-16:00",
    "timezone": "America/New_York"
  },

  "risk": {
    "stop_loss_pct": -5.0,
    "stop_loss_chop_pct": -8.0,         // wider stop in chop
    "profit_target_pct": 10.0,
    "profit_target_is_guide": true,
    "max_position_pct": 20.0,
    "max_positions": 15,
    "max_micro_cap_positions": 1,
    "conviction_floor": 0.60,
    "max_portfolio_risk_pct": 5.0
  },

  "regimes": {
    "chop_allow_new_entries": false,
    "chop_oversold_rsi_threshold": 37,
    "chop_max_add_shares": 2,
    "sustainable_allow_new_entries": true,
    "sustainable_entry_rsi_max": 50,
    "exhausted_trim_above_pct": 5.0,
    "exhausted_raise_stops_be": true
  },

  "trim": {
    "cyclical_trim_pct": 0.25,
    "cyclical_pt_trigger": 0.10,
    "curve_trim_pct": 0.50,
    "curve_trim_threshold_bps": 25,
    "defensive_let_run": true
  },

  "conviction_rules": {
    "adjust_conviction_premarket": false,
    "entry_rsi_max_chop": 45
  },

  "micro_cap": {
    "enabled": true,
    "max_shares_per_ticker": 4,
    "max_cost_per_ticker": 0.50,
    "max_total_exposure": 2.00,
    "stop_loss_pct": -8.0
  },

  "synthesis": {
    "lookback_n_entries": 10
  },

  "alpaca": {
    "base_url": "https://paper-api.alpaca.markets",
    "time_in_force": "day",
    "order_type": "market"
  },

  "journal": {
    "path": "journal/",
    "format": "YYYY-MM-DD.md",
    "git_commit": true
  }
}
```

Params.json evolves over time. The nightly bumps values as the trader learns. The git log tracks every change. New params are added as the agent discovers they need them ("I don't know the names yet because I'm not a trader").

---

## 6. Cron Jobs

| Name | Schedule | Session | Purpose |
|------|----------|---------|---------|
| **tick-dispatcher** | `*/5 9-15 * * 1-5` (Mon-Fri) | Isolated | Dispatches "Market tick — read tick_prompt.md" to all trader sessions |
| **aldridge-nightly-maintenance** | `30 16 * * 1-5` | Isolated | Three-step nightly (journal → synthesize → evolve) |
| **weekly-trader-research** | `0 10 * * 0` (Sundays) | Isolated | Searches web for LLM trading bot papers, techniques, tools. Writes to `design/trader-research/YYYY-MM-DD.md` |
| **tool-request-watchdog** | `0 17 * * 1-5` | Isolated | Scans journals for "this should be code" / "I need a tool" patterns, creates workboard cards |
| **memory-dreaming-promotion** | `0 3 * * *` (daily) | Isolated | OpenClaw built-in: promotes short-term memory to MEMORY.md |

---

## 7. Design Decisions

### Persistent vs. Isolated Sessions
- **Trading ticks**: Persistent session per trader. Context stays warm across ticks. No re-reading stable files.
- **Tick dispatcher**: Isolated. Zero context — just relays messages to traders.
- **Nightly maintenance**: Isolated. Cold start forces disciplined file writing.
- **Research/watchdog crons**: Isolated. Self-contained tasks that don't need session context.

### Why Files (not a database) for Phase 1
- Files are human-readable and git-trackable.
- Database schema is discovered organically — you don't know what params you need until the agent tells you.
- Git provides rollback, history, and branching for free.
- Migration to PG (Phase 2) is a mechanical step once the schema stabilizes.

### Strategy Evolution (not branch-swapping)
- Single strategy.md per trader that evolves. Versioned (`ald.strat:v1.0`).
- No per-condition micro-branches or switchboards.
- The agent refines its general philosophy, not a decision tree.
- Experimental strategies use `x-` prefix (`ald.strat:x-momentum`). Try for 5 trades, promote or revert.

### Strategy Selection Is Vibes-Based
- No objective measurement until the historical trader exists (Phase 7).
- The nightly explains why a change might help and whether it could be luck.
- The human (Rafael) makes the final call on strategy changes.
- This is acknowledged as temporary — Phase 7 adds proper parameter estimation.

---

## 8. Future Phases (from MVP.md)

See `design/MVP.md` in the repo for full details.

| Phase | What | When |
|-------|------|------|
| **1** ✅ | Aldridge trades locally (SQLite, direct Alpaca, params.json, strategy.md, git) | This weekend |
| **2** | Add PG on docker.klo (migrate from SQLite) | Week 2 |
| **3** | Add Terminal MCP server (trades through MCP instead of direct Alpaca) | Week 2 |
| **4** | Add Kairos as second trader (same file structure) | Week 3 |
| **5** | Nightly GPU optimization for Kairos | Week 3-4 |
| **6** | Leaderboard, news poller, multi-agent (Stonks joins) | Week 4+ |
| **7** | Practice mode + historical trader + parameter estimation | Week 5+ |

Phase 7 is the long-term payoff: replay historical 5-min bars through the agent's tick loop, score decisions vs actual outcomes, estimate optimal params from historical performance. The 5-min bar data already exists in the paper-trading-rebuild PG database on docker.klo.

---

## 9. Git Workflow

### Branch Strategy
- `main` — production. Currently Phase 1 (Aldridge only).
- `v4/feature-x` — feature branches off main. Example: `v4/kairos-scaffold`, `v4/historical-trader`

### Auto-Commit Skill
Every file modification is committed immediately:
```bash
cd /home/openclaw/paper-trading-agents
git add aldridge/
git commit -m "aldridge: YYYY-MM-DD — description of change"
git push
```
No gates, no approvals. Git history is the rollback mechanism. Push failures are non-fatal (commit is local, retried next tick).

### What Gets Committed
- strategy.md changes (strategy evolution)
- params.json changes (param tuning)
- active.md updates (working memory)
- positions/*.md changes (thesis updates)
- tick_prompt.md changes (loop evolution)
- journal entries (EOD diary)

---

## 10. Key Files in Repo

| File | Purpose |
|------|---------|
| `aldridge/AGENTS.md` | Aldridge identity + nightly instructions |
| `aldridge/SOUL.md` | Edmund Whitfield persona |
| `aldridge/TOOLS.md` | Tool references |
| `aldridge/HEARTBEAT.md` | Tick checklist |
| `aldridge/tick_prompt.md` | **The trading loop** |
| `aldridge/strategy.md` | Constitution (v1.0) |
| `aldridge/params.json` | All parameters |
| `aldridge/strategies/active.md` | Working memory |
| `aldridge/journal/2026-07-17.md` | Example daily journal |
| `aldridge/skills/auto-commit.md` | Auto-commit skill |
| `aldridge/scripts/executor.py` | Alpaca order executor |
| `design/MVP.md` | Full phased deployment plan |
| `design/trader-research/` | Weekly research output directory |
