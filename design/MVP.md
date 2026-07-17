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
| GPU compute / ML | Aldridge doesn't need it. Manual param tuning first. |
| MCP Server | Direct Alpaca calls via existing skill. Keep it simple. |
| News aggregation | Manual for now. Aldridge checks macro + fundamentals. |
| Historical data accumulation | Later. Don't build what isn't needed. |
| Multi-agent registration | Later. One agent. |
| Virtual agents | Later. One strategy. |

### What We Keep

| Keep | Why |
|------|-----|
| **Aldridge** | Simplest trader. Value picks on 5-month time scale. Dynamic watchlist. |
| **params.json** | One file. Read every tick. Write via agent commits. |
| **strategy.md** | One file. Read every tick. Updated during nightly maintenance. |
| **journal.md** | Local, append-only. Git commit tag per entry. |
| **Git** | Branch, change strategy, commit with rationale, correlate performance. |
| **Nightly maintenance** | Cron job. Trim files, reflect, update strategy, commit. |
| **Competition mindset** | Standing order. Always present. |
| **sqlite** | Local DB for now. Minimal, fast, no PG overhead. |

---

## Phase 1: Aldridge Trades Locally (THIS WEEKEND)

### Goal

Aldridge reads params.json + strategy.md, picks stocks, places trades via Alpaca, writes journal.md, commits to git. All on local SQLite. Works end-to-end.

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
  Reads:  Terminal.get_quotes() → or direct Alpaca call
  Writes: journal.md (with git commit tag)
  Writes: aldridge.db (positions, decisions)

Nightly maintenance (16:30 ET — cron, 30 min):
  Reads:  journal.md from today
  Reads:  strategy.md (what was I trying?)
  Reads:  params.json (what params was I using?)
  Writes: updated strategy.md (new thesis)
  Writes: updated params.json (adjusted params)
  Writes: git commit with rationale
  Writes: aldridge.db (reflection entry)
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
