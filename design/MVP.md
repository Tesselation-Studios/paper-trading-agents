# MVP Phased Deployment Plan

> **Status**: Draft | **Date**: 2026-07-17
> **Goal**: One trader (Aldridge) trading and learning on local SQLite, validated at every step, deployed asap.

## Core Principle

Everything we do must be **validated before moving on**. No building the next layer until the current one actually works end-to-end with real trades. Strip it back, prove it, then expand.

## Phase 0: Simplify & Strip Back

### What We're Not Doing (for now)

| Out | Why |
|-----|-----|
| Multi-trader system | One trader first. Aldridge. |
| PostgreSQL on docker.klo | Local SQLite. Avoid VM disk IO bottleneck. |
| Leaderboard / Dashboard | Watch on Alpaca directly. Fastest feedback loop. |
| GPU compute / ML training | Aldridge evolves via reflection + params, not GPU. |
| MCP Server | Direct Alpaca calls via existing skill. Keep it simple. |
| News aggregation | Manual for now. Aldridge checks macro + fundamentals. |
| Historical data accumulation | Later. Don't build what isn't needed. |
| Multi-agent registration | Later. One agent. |
| Virtual agents | Later. One strategy. |

### What We Keep — Including the Learning System

| Keep | Why |
|------|-----|
| **Aldridge** | Simplest trader. Value picks on 5-month time scale. Dynamic watchlist. |
| **params.json** | One file. Read every tick. Write via agent commits. **Params evolve.** |
| **strategy.md** | One file. Read every tick. **Updated when agent learns something new.** |
| **journal.md** | Local, append-only. Git commit tag per entry. Reflection data. |
| **Git** | Branch, change strategy, commit with rationale, correlate performance. |
| **Nightly maintenance** | Cron job. Trim files, **reflect, evolve strategy, commit.** |
| **Competition mindset** | Standing order. Always present. |
| **sqlite** | Local DB for now. Minimal, fast, no PG overhead. |
| **Learning & reflection loop** | Every tick includes "what did I learn?" Every night includes strategy evolution. |

### Learning System is NOT Deferred

The learning system — params evolution, strategy.md updates, reflection, git-based iteration — is **baked into Phase 1.** The only ML capabilities deferred are GPU-based (model training, parameter sweeps), because Aldridge doesn't need them to start learning.

Aldridge starts with a value strategy, but **he is not limited to it.** If he reflects and genuinely believes he can do better with momentum signals, sentiment analysis, or something else entirely, he can evolve his strategy.md and params.json to try it. The git commit trail tracks every evolution.

> **Principle: Traders are not their starting strategy.** The initial prompt is a suggestion, not a cage.

### Memory Features Per Phase

| Feature | What It Does | Phase | Config |
|---------|-------------|-------|--------|
| **Built-in memory** (MEMORY.md, daily notes) | Manual recall via `memory_search` / `memory_get` | Phase 1 | Default (no config needed) |
| **Dreaming** | Auto-consolidate lessons into MEMORY.md overnight | Phase 1 (enable now) | `plugins.entries.memory-core.config.dreaming.enabled: true` |
| **Active memory plugin** | Auto-inject relevant memories before replies | Phase 3+ | `plugins.entries.active-memory.enabled: true` |

**For Phase 1:** Enable dreaming from the start. The agents will automatically distill lessons into MEMORY.md without manual curation. Active memory adds latency and complexity we don't need yet.

---

## Phase 1: Aldridge Trades Locally (THIS WEEKEND)

### Goal

Aldridge reads params.json + strategy.md, picks stocks, places trades via Alpaca, writes journal.md with reflection, evolves strategy based on what he learns, and commits changes to git. All on local SQLite. Works end-to-end.

**Aldridge starts as a value trader but can evolve.** If he reflects and believes a different approach would work better, he changes his strategy.md, updates params.json, and commits the change. The only thing not available yet is GPU-based ML training.

### Stack

```
.41 VM (local)
├── Aldridge (existing AGENTS.md — updated for new flow)
├── params.json            ← read every tick
├── strategy.md            ← read every tick
├── journal.md             ← written after every trade
├── aldridge.db (SQLite)   ← positions, decisions, journal entries
├── tick_cron.py           ← dispatches Aldridge (already exists)
├── sync_alpaca_positions.py ← positions → SQLite
└── Git                     ← weekly strategy commits

↕

Alpaca (paper trading API — watch performance there)
```

### Files Aldridge Reads/Writes

```
Every tick:
  Reads:  strategy.md, params.json
  Reads:  get_quotes() → via direct Alpaca call
  Writes: journal.md (with git commit tag + reflection)
  Writes: aldridge.db (positions, decisions)

Nightly maintenance (16:30 ET — cron, 30 min):
  Reads:  journal.md from today (what did I learn?)
  Reads:  strategy.md (what was I trying?)
  Reads:  params.json (what params was I using?)
  Reflects: "Did the strategy work? What should change?"
  Writes: updated strategy.md (new thesis — can be completely different)
  Writes: updated params.json (adjusted params)
  Writes: git commit with rationale
  Writes: aldridge.db (reflection entry)

Key: Aldridge can evolve to use momentum, sentiment, breakouts — whatever
he genuinely believes will work better. The strategy.md is his hypothesis.
The git log tracks which hypotheses worked.
```

### Verification

```markdown
✅ [ ] Aldridge places a real trade via Alpaca
✅ [ ] params.json updates every tick
✅ [ ] strategy.md is read and respected
✅ [ ] journal.md records every trade with git commit
✅ [ ] Nightly maintenance runs and commits changes
✅ [ ] SQLite stores all decisions and positions
✅ [ ] Alpaca dashboard shows correct positions
✅ [ ] Restart: Aldridge resumes with same strategy.md + params.json
```

**Validation gate:** Aldridge trades for one full day. All checks pass. If any check fails, fix before moving to Phase 2.

### Strategy Evolution — Not Micro-Branches

The strategy is a **general approach that gets refined over time**, not a switchboard of per-condition playbooks. The agent doesn't swap branches every tick based on market conditions. Instead, the agent develops a coherent philosophy and refines it through reflection.

#### What strategy.md Looks Like

```markdown
# Aldridge — Strategy v3

## Philosophy
I buy fundamentally sound stocks at a discount and hold until they reach
fair value. I use macro indicators to find sectors that are undervalued
relative to the broader market. I don't chase momentum.

## Current Approach
- Screen: P/E < sector avg, debt/equity < 0.5, positive free cash flow
- Entry: Stock is within 5% of 52-week low (value play)
- Exit: Stock reaches sector-average P/E or 10% gain, whichever comes first
- Position: 15% of portfolio max, 4 positions max
- Hold: ~5 months target

## What I'm Learning
- [2026-07-16] Range trades on SPY work well (+1.2%). But that's not my
  game. I'm a value trader. I'll stick to fundamentals.
- [2026-07-15] NVDA broke below support. I don't trade tech anyway.
- [2026-07-14] Discovered: defensive sectors (utilities, healthcare) hold
  up better in choppy markets. Maybe increase allocation there.

## Posture Note
- SPY is choppy. I'm not changing my strategy — I'm just being patient.
  Value plays take time. I'll wait for the right entry.
```

#### What Evolves (Not Just strategy.md)

When the agent learns something, the change can go anywhere:

| File | What Changes | Example |
|------|-------------|---------|
| **strategy.md** | Philosophy, approach, posture | "I'm learning that defensive sectors work better in choppy markets" |
| **AGENTS.md** | Prompt instructions, tools, rules | "Add rule: if sector is defensive, allow 20% position instead of 15%" |
| **params.json** | Numerical params | `stop_loss_pct: 5 → 3`, `max_cash_pct: 0.8 → 0.6` |
| **Skills (SKILL.md)** | New capabilities | "Created a new skill for screening defensive sectors" |
| **TOOLS.md** | Tool references | "Added a new external data source for sector rotation" |
| **Workspace files** | Anything the agent uses | New reference docs, watchlists, helper scripts |

The key: the agent isn't writing a bunch of micro-playbooks. They're refining their **general approach** — and sometimes that means updating their prompt, their tools, or their skills, not just their strategy file.

#### Versioning: Strategy Version, Not Branch Versions

Instead of per-condition branches, version the **overall strategy**:

```
ald.strat:v1.0   → Initial value strategy
ald.strat:v1.1   → Tweaked P/E screen threshold
ald.strat:v2.0   → Major revision: added sector rotation filter
```

Where it appears:
| File | How |
|------|-----|
| **strategy.md** | `# Aldridge — Strategy v3` |
| **params.json** | `"strategy_version": "ald.strat:v2.0"` |
| **journal.md** | `Strategy: ald.strat:v2.0` on every trade |
| **Git commit** | `ald: strat v2.0 - add sector rotation filter` |
| **SQLite** | `decisions.strategy_version` column |

Experimental versions start with `x-`:
```
ald.strat:x-momentum   → Experiment: try momentum instead of value, just for 5 trades
```

If the experiment works, promote it to a numbered version. If not, the agent writes a reflection about why it failed and goes back to the previous strategy.

#### Baked Into the Agent Prompt

```markdown
## Learning & Evolution
- You have a general strategy in `strategy.md`. Read it every tick.
- You are NOT limited to this strategy. If you genuinely believe a different
  approach would work better, change it.
- When you change your strategy, also update AGENTS.md, params.json, and
  any skills or tools that need to change.
- Version your strategy with `ald.strat:v{major}.{minor}`.
- After every trade, write a one-line reflection in journal.md.
- During nightly maintenance: review your strategy. Is it working? What
  would make it better? Update files, commit changes.
```

---

## Phase 2: Add PG (Week 2)

### What Changes

Replace local SQLite with PostgreSQL on docker.klo. Add the Terminal MCP server for database operations.

### Migration

```sql
-- Create trading schema on docker.klo PG
CREATE SCHEMA IF NOT EXISTS aldridge;

CREATE TABLE aldridge.positions (...);
CREATE TABLE aldridge.decisions (...);
CREATE TABLE aldridge.journals (...);

-- Copy from local SQLite
INSERT INTO aldridge.positions SELECT * FROM sqlite_positions;
```

### What Stays Same

- params.json (still read every tick — reads from PG now)
- strategy.md (still read every tick — committed to git)
- journal.md (still written after every trade — mirrored to PG)
- Alpaca is still the UI for performance

### Verification

```markdown
✅ [ ] Aldridge trades still work (same flow)
✅ [ ] PG has same data as SQLite after migration
✅ [ ] params.json syncs from PG every tick
✅ [ ] journal entries appear in PG
✅ [ ] SQLite can be dropped
```

---

## Phase 3: Add Terminal MCP Server (Week 2-3)

### What Changes

Deploy the MCP server on docker.klo. Aldridge connects via MCP instead of direct Alpaca calls.

```
Aldridge → MCP (docker.klo:5001) → Alpaca
                              → PG
```

### Tools Aldridge Uses

- `get_quotes()`, `get_macro()` — data
- `submit_order()` — execution (replaces direct Alpaca)
- `record_journal()` — structured journals
- `get_positions()`, `get_portfolio()` — portfolio view

### What Doesn't Change

- params.json still read every tick
- strategy.md still read every tick
- journal.md still local + git committed
- Nightly maintenance still runs

### Verification

```markdown
✅ [ ] Aldridge trades through MCP instead of direct Alpaca
✅ [ ] Same trades as Phase 1 (compare logs)
✅ [ ] PG has all data
✅ [ ] params.json still updates every tick
✅ [ ] Rollback: swap MCP config → direct Alpaca
```

---

## Phase 4: Add Kairos (Week 3)

### What Changes

Kairos is added as the second trader. Same params.json + strategy.md + journal.md + git flow. Kairos is more active (momentum, shorter time scale, more trades) — this tests the system with higher throughput.

Kairos can use the same MCP server, same PG. Just a different API key scoped to its Alpaca account.

### Verification

```markdown
✅ [ ] Kairos places trades
✅ [ ] Aldridge still trades (no regression)
✅ [ ] Both write to same PG without conflicts
✅ [ ] Both read params.json from same source
✅ [ ] Both have independent strategy.md files
```

---

## Phase 5: Add Nightly GPU Optimization (Week 3-4)

### What Changes

Kairos now uses GPU compute during nightly maintenance. Aldridge doesn't need it.

- Overnight cron: Kairos submits param optimization job to GPU
- GPU returns optimal params
- Kairos updates strategy.md and params.json
- Next day: trades with improved params

### Verification

```markdown
✅ [ ] GPU health check returns OK
✅ [ ] GPU training job completes
✅ [ ] Params are updated and reflected in params.json
✅ [ ] Kairos performance vs baseline (compare win rates)
```

---

## Phase 6: Add Leaderboard + News + Multi-Agent (Week 4+)

### What Changes

- Leaderboard reads from PG (wasn't needed before — Alpaca was enough)
- News poller starts filling news_archive
- Registration API for external agents
- Stonks joins as third trader

### Verification

```markdown
✅ [ ] Leaderboard shows all traders
✅ [ ] News archive has entries
✅ [ ] Stonks trades with own strategy
✅ [ ] All three traders coexist
```

---

## Timeline

```
THIS WEEKEND
┌─────────────────────────────────────────────────────────┐
│ Phase 1: Aldridge trades locally                        │
│ - SQLite, direct Alpaca, params.json, strategy.md, git  │
│ - Validate: trades, params sync, nightly maintenance    │
└─────────────────────────────────────────────────────────┘

WEEK 2
┌─────────────────────────────────────────────────────────┐
│ Phase 2: Add PG on docker.klo                           │
│ Phase 3: Add Terminal MCP server                        │
│ - Migrate from SQLite to PG                             │
│ - Aldridge trades through MCP                           │
│ - Validate: same trades, PG has data                    │
└─────────────────────────────────────────────────────────┘

WEEK 3
┌─────────────────────────────────────────────────────────┐
│ Phase 4: Add Kairos                                     │
│ Phase 5: Add GPU optimization                           │
│ - Second trader, higher throughput                       │
│ - Nightly param opt via GPU                             │
│ - Validate: no regression on Aldridge                   │
└─────────────────────────────────────────────────────────┘

WEEK 4+
┌─────────────────────────────────────────────────────────┐
│ Phase 6: Leaderboard, news, multi-agent                 │
│ - Stonks, registration, news poller                     │
│ - Full system as designed in trading-terminal.md        │
└─────────────────────────────────────────────────────────┘
```

---

## Rollback

Every phase has a two-way door:

| Phase | Rollback |
|-------|----------|
| Phase 1 | Reset params.json + strategy.md to last good commit |
| Phase 2 | Point back to SQLite (data still there) |
| Phase 3 | Remove MCP config, use direct Alpaca |
| Phase 4 | Disable Kairos cron, Aldridge continues |
| Phase 5 | Disable GPU cron, params fall back to previous |
| Phase 6 | Any component can be disabled independently |

---

## Key Files (all phases)

| File | Purpose | Phase |
|------|---------|-------|
| `aldridge/params.json` | Current trading params | Phase 1 |
| `aldridge/strategy.md` | Current playbook | Phase 1 |
| `aldridge/journal.md` | Running diary with git commit tags | Phase 1 |
| `aldridge/AGENTS.md` | Agent prompt (updated for new flow) | Phase 1 |
| `aldridge.db` | Local SQLite | Phase 1 |
| `docker.klo:5432/aldridge.*` | PG tables | Phase 2 |
| `docker.klo:5001` | MCP Server | Phase 3 |
| `kairos/params.json` | Kairos params | Phase 4 |
| `kairos/strategy.md` | Kairos playbook | Phase 4 |
| `kairos/journal.md` | Kairos diary | Phase 4 |

---

## Why This Order

1. **Aldridge first** — he's the simplest. Fewest trades, longest time scale, least complexity. Perfect for proving the params.json + strategy.md + journal.md + git flow works end-to-end.

2. **Local SQLite first** — no VM disk IO bottleneck for a single trader's data. No PG dependency to debug. Fastest path to a working trade.

3. **PG/Terminal second** — once Aldridge trades reliably, add the infrastructure he needs. The migration is low-risk because we're just moving data, not changing the trading logic.

4. **Kairos third** — adds throughput and ML. By then the infrastructure is proven.

5. **GPU fourth** — Kairos gets smarter. Aldridge unaffected.

6. **Everything else fifth** — leaderboard, news, multi-agent. Nice-to-haves that don't block trading.

---

*This is the living plan. Update as each phase completes.*
