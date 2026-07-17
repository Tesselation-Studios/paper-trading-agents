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

### Strategy Playbook Design

Aldridge's `strategy.md` is a **decision tree, not a single strategy.** He reads it every tick, assesses the current market, and picks the right branch.

#### Structure

```markdown
# Aldridge — Current Strategy

## Market Assessment (read every tick)
- SPY trend: {uptrend / downtrend / sideways}  ← assess from RSI + MACD
- Volatility: {low / normal / high}              ← assess from BB width
- Confidence: {high / medium / low}              ← assess from signal agreement

## Playbook (pick one branch based on assessment)

### If Uptrend + Low Vol → "Accumulate"
- Buy dips to mid-BB, sell at upper BB
- Position: 15% of portfolio
- Hold: 5-20 days

### If Uptrend + High Vol → "Trend Follow"
- Buy breakouts above upper BB
- Position: 10% of portfolio
- Tighter stops (3%)
- Hold: 3-10 days

### If Sideways + Low Vol → "Range Trade"
- Buy at lower BB, sell at mid-BB
- Position: 8% of portfolio
- Hold: 2-5 days

### If Sideways + High Vol → "Wait"
- Don't enter. Cash is position.
- Review watchlist for breakouts

### If Downtrend → "Defend"
- No new positions
- Cut any position below lower BB
- Cash > 80%

## Current Posture
- Regime: Sideways + Normal Vol
- Active play: Range Trade
- Started: 2026-07-15
- Win rate this play: 60% (6 of 10 trades)

## Learning Notes
- Last week: Range Trade worked on SPY (+1.2%), not on NVDA (-3%)
- Hypothesis: Range Trade only works on index ETFs, not individual stocks
- Testing: next range trade will be SPY-only
```

#### Keeping It Nimble

| Rule | Detail |
|------|--------|
| **Branch size** | Max 10 lines per playbook. If it's longer, it's too complex. |
| **Win rate tracking** | Agent tracks win rate per branch. < 40% after 10 trades ⇒ archive. |
| **Expiration** | If a branch hasn't triggered in 30 days, archive it (git keeps the history). |
| **Experiments** | New branches start as experiments. Test 5 times. Keep or delete. |
| **Current posture** | Always visible at the top so you know what's active and why. |

#### Branch Naming Scheme

Every playbook branch gets a unique, stable identifier so it can be referenced across files, queried in the DB, and jumped to via git.

##### Format: `{trader}.{branch_slug}:v{major}.{minor}`

| Component | Rules | Example |
|-----------|-------|---------|
| `trader` | 3-letter abbreviation | `ald` (Aldridge), `kai` (Kairos), `stk` (Stonks) |
| `branch_slug` | 3-12 lowercase chars, underscores | `range_trade`, `accumulate`, `defend` |
| `v{major}.{minor}` | Semver | `v1.0` (initial), `v1.1` (tweak), `v2.0` (major revision) |

##### Branch IDs for Aldridge's Playbook

```
ald.acc:v1.0      → Accumulate (uptrend + low vol)
ald.tfl:v1.1      → Trend Follow (uptrend + high vol)
ald.rng:v2.0      → Range Trade (sideways + low vol)
ald.wat:v1.0      → Wait (sideways + high vol)
ald.dfn:v1.3      → Defend (downtrend)
```

##### Where Identifiers Appear

| File | How |
|------|-----|
| **strategy.md** | `### If Sideways + Low Vol → "Range Trade" [ald.rng:v2.0]` |
| **params.json** | `"active_branch": "ald.rng:v2.0"` |
| **journal.md** | `Branch: ald.rng:v2.0` on every trade entry |
| **Git commit** | `ald: range_trade v1.1 - tighten stops 5%→3%` |
| **SQLite** | `decisions.branch_id` column |

##### How to Jump Between Branches

When the market assessment changes, the agent:

```
1. Reads market: SPY trend → sideways, volatility → low
2. Checks strategy.md: "Sideways + Low Vol → Range Trade [ald.rng:v2.0]"
3. Updates params.json: active_branch: "ald.rng:v2.0"
4. Journal: "Jump: ald.acc:v1.0 → ald.rng:v2.0 (market shifted sideways)"
5. Trades using Range Trade rules
```

The jump is instant — agent reads the new branch's rules and executes. No restart.

##### Branch Versioning in Git

```bash
# Initial
$ git commit -m "ald: add range_trade playbook [ald.rng:v1.0]"

# Minor tweak (stops)
$ git commit -m "ald: range_trade v1.1 - tighten stops 5%→3%"

# Major revision (entry criteria)
$ git commit -m "ald: range_trade v2.0 - buy at lower BB + MACD confirmation"
```

##### Performance by Branch

```sql
SELECT branch_id, COUNT(*) as trades, AVG(pnl) as avg_pnl
FROM aldridge.decisions GROUP BY branch_id ORDER BY avg_pnl DESC;
-- ald.rng:v2.0   | 3 trades  | +1.5%  ← best
-- ald.rng:v1.0   | 8 trades  | +1.2%
-- ald.acc:v1.0   | 12 trades | +0.5%
-- ald.dfn:v1.3   | 4 trades  | -0.3%  (defensive, expected)
```

##### Experimental Branches

Prefix with `x-` until promoted:

```
ald.rng:x-gap-up      → Experiment: range trade on gap-up openings
kai.mom:x-vwap        → Experiment: momentum entry with VWAP confirmation
```

Promote to numbered version when proven: `ald.rng:x-gap-up` → `ald.rng:v3.0`.

##### Quick Reference

| Pattern | Example | Meaning |
|---------|---------|---------|
| `{3l}.{slug}:v{maj}.{min}` | `ald.rng:v2.0` | Stable, versioned |
| `{3l}.{slug}:x-{name}` | `ald.rng:x-gap-up` | Experimental |
| `{3l}.{slug}` | `ald.wat` | Version omitted = latest active |

#### Evolution Path

```
Week 1:  1 playbook (value: buy undervalued stocks, hold 5 months)
Week 2:  2 playbooks (value + range trade on SPY)
Week 3:  3 playbooks (value + range + trend follow)
Month 2: 5 playbooks, each refined by 10+ trades of data
Month 3: Branch pruning — the 2 weakest playbooks removed
Month 6: 3 highly refined playbooks, each with 50+ trades of data
```

The agent learns which playbooks work, prunes the losers, and deepens the winners. The `strategy.md` stays nimble because it's always under active management.

#### Same for Stonks and Kairos

When they join in later phases, they get the same structure:

```markdown
# Kairos — Current Strategy

## Market Assessment
- {same trend/vol/confidence assessment}

## Playbook
### If Momentum > Threshold + High Volume → "Momentum Entry"
### If Sentiment Divergence + Low Price → "Contrarian Bet"
### If No Clear Signal → "Wait / Tighten"
### If Regime Chop → "Defensive"

## Current Posture
- {active play + win rate}

## Learning Notes
- {lessons from recent trades}
```

The playbook structure is universal. Only the playbook contents differ per trader.

#### Baked Into the Agent Prompt

Every trader's AGENTS.md includes this instruction:

```markdown
## Strategy
- Read `strategy.md` at the start of every tick
- Assess current market (trend, volatility, confidence)
- Pick the matching playbook branch
- Execute according to that branch's rules
- After the trade, reflect: did this playbook perform as expected?
- During nightly maintenance: update win rates, prune dead branches, add experiments
```

This makes the playbook system immutable from the agent's perspective — they read it, follow it, and update it. The strategy.md is always the source of truth for "what should I do right now?"

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
