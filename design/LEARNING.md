# Learning Paradigm — LLM + ML Feedback Loop

> **Status**: Draft | **Date**: 2026-07-16
> **Parent**: [trading-terminal.md](trading-terminal.md) — Design doc
> **PR**: [#1](https://github.com/Tesselation-Studios/paper-trading-agents/pull/1)

---

## The Core Loop

```
                    ┌─────────────────────────────────────────────┐
                    │         THE MARKET                           │
                    │  (real during day, simulated at night)       │
                    └──────────────────┬──────────────────────────┘
                                       │
                    Trade results feed │ back into learning
                                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        AGENT WORKSPACE                              │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐ │
│  │  strategy.md     │  │  params.json     │  │  journal.md      │ │
│  │  "This is my     │  │  {stop_loss: 5,  │  │  ## 2026-07-16   │ │
│  │   approach for   │  │   max_cash: 0.8, │  │  Commit: abc123  │ │
│  │   this market."  │  │   model: hmm_v2} │  │  Trade: bought   │ │
│  └──────────────────┘  └──────────────────┘  │  NVDA because...  │ │
│         ▲                       ▲            │  Reflection: ...  │ │
│         │                       │            └──────────────────┘ │
│         └───────┬───────────────┘                    │            │
│                 │                                     │            │
│         strategy.md + params.json              journal.md with     │
│         are read every tick                     git commit tag     │
└─────────────────│───────────────────────────────────│──────────────┘
                  │                                   │
                  │ Git commit includes:               │
                  │ strategy.md, params.json,          │
                  │ and links to journal               │
                  ▼                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        GIT REPO                                     │
│                                                                     │
│  Commit abc123: "Kairos: tighten stops in choppy market"            │
│    → strategy.md (v3)  → params.json (stop_loss: 3, max_cash: 0.3) │
│    → Journal: "Cut NVDA at -3%, market choppy. Tightening stops."   │
│                                                                     │
│  Later: correlate performance vs commit history                     │
│  "Commits with stop_loss > 5 underperformed in Q3"                  │
└─────────────────────────────────────────────────────────────────────┘
                  ▲
                  │
┌─────────────────┴────────────────────────────────────────────────────┐
│                      POSTGRESQL                                      │
│                                                                      │
│  trading.decisions  ← source of truth for all decisions              │
│  trading.journals   ← structured journals from record_journal()      │
│  trading.positions  ← position history                               │
│  trading.params     ← current + historical params per trader         │
│  trading.ml_jobs    ← GPU compute job history                        │
│  trading.signals_v2 ← all signals from all traders                   │
└──────────────────────────────────────────────────────────────────────┘
```

The key insight: **PG is the immutable source of truth.** Git is the audit trail for prompts and params. Local files are what the agent actually reads and writes moment-to-moment. All three are synchronized, not duplicated.

---

## 1. The Three Data Stores

| Store | Purpose | Source of Truth For | Written By | Read By |
|-------|---------|---------------------|------------|---------|
| **PostgreSQL** (docker.klo) | Permanent record of everything | Decisions, journals, positions, orders, params | Terminal `record_*` tools | Leaderboard, Terminal tools |
| **Git** (paper-trading-agents repo) | Prompt/strategy/param version history | strategy.md, params.json, AGENTS.md | Agent commits during nightly maintenance | Agent on next tick (reads strategy.md) |
| **Local files** (agent workspace) | What the agent works with moment-to-moment | strategy.md (current approach), params.json (current params), journal.md (running diary) | Agent writes, tick_cron syncs | Agent reads every trading tick |

### Synchronization Flow

```
Every tick:
  PG.params  ───sync──→  params.json  (tick_cron or Terminal pushes current params)
                          strategy.md   (read from git, checked out on agent start)
                          journal.md    (agent appends locally)

Nightly maintenance:
  journal.md ───record_journal()──→ PG.journals  (structured entries)
  params.json ────git commit──────→ Git           (with commit message)
  strategy.md ────git commit──────→ Git           (if changed)
  
  Then: PG is fully synced. Git has the snapshot. Local files are clean.
```

---

## 2. The Params Flow

### Source of Truth: PostgreSQL

The Terminal stores all trading parameters in PG:

```sql
CREATE TABLE trading.params (
    id BIGSERIAL PRIMARY KEY,
    trader_id TEXT NOT NULL,
    key TEXT NOT NULL,                      -- 'stop_loss_pct', 'max_cash_pct', 'max_positions', etc.
    value JSONB NOT NULL,                   -- e.g. 5, 0.8, or {"model": "hmm_v2", "n_states": 4}
    active BOOLEAN DEFAULT TRUE,            -- only one active set per trader
    git_commit TEXT,                        -- which commit introduced this value
    created_at TIMESTAMPTZ DEFAULT NOW(),
    superseded_at TIMESTAMPTZ               -- when it was replaced
);
```

### On Every Trading Tick

```
1. Terminal.get_portfolio() returns {value, cash, positions}
2. tick_cron (or the agent) calls Terminal.get_params(trader)
3. Terminal reads the active params from PG
4. Terminal writes params.json to the agent's workspace:
   {
     "trader": "kairos",
     "as_of": "2026-07-16T16:00:00Z",
     "git_commit": "abc123",
     "params": {
       "stop_loss_pct": 5,
       "max_cash_pct": 0.8,
       "max_positions": 5,
       "entry_gate": {
         "max_position_pct": 15,
         "min_confidence": 0.3,
         "required_signals": ["momentum", "sentiment"]
       },
       "ml_model": "hmm_spy_v2"
     }
   }
5. Agent reads params.json at the start of every tick
```

### When Params Change

```
1. Agent proposes a change during nightly maintenance:
   "I think stop_loss should be tighter — 3% instead of 5%"

2. Agent writes a new strategy.md explaining why:
   "Market is choppy. Tighter stops prevent drawdown during whip. If I'm right,
    I'll exit positions faster but preserve capital. If I'm wrong, I'll miss
    some recoveries but avoid catastrophic losses. Testing for 5 trading days."

3. Agent commits:
   git add kairos/strategy.md kairos/params.json
   git commit -m "Kairos: tighten stop_loss from 5% to 3% in choppy market
   
   Rationale: Choppy regime detected for 3 consecutive sessions. Tighter
   stops reduce whipsaw damage. Will revert if regime changes back to
   sustainable. Parameters unchanged: max_cash 80%, max_positions 5."

4. Agent calls Terminal.set_params({...}) with the new values
5. Terminal writes new active params to PG with git_commit field
6. Next tick: params.json gets the new values
```

---

## 3. The Journal Flow

### Local Journal (agent's workspace)

The agent writes a running diary in `journal.md`:

```markdown
# Kairos — Trading Journal

## 2026-07-16

### Commit: a1b2c3d (strategy.md v3, params stop_loss=3%)

**Tick 14:23 ET — Regime: CHOPPY, Confidence: 30%**
- SPY $751.18 (-0.48%), QQQ $707.15 (-1.47%)
- NVDA -2.63% — bullish MACD divergence intact
- Decision: HOLD ALL — 71% cash appropriate
- Reflection: MACD divergence on NVDA and AMZN is interesting. If they reverse
  above mid-BB tomorrow, consider adding. If they break below lower BB, cut.
- Time spent: 45s. Fast tick. Good.

**Tick 15:51 ET — Close ritual**
- SOFI -3.55%, momentum -6.35, RSI 44
- Decision: CUT SOFI (10 shares)
- Reflection: Student loan default headwind is real. Should have cut earlier
  when it hit watchlist. Next time: act on first watchlist alert, don't wait.
- Lesson: If a position hits the watchlist on two consecutive ticks, cut it.

**Nightly Maintenance (16:32 ET)**
- Stopped journaling at 16:00 ET for maintenance
- Read Stonks' journal: "Bullish on WSB momentum, entered PLTR"
  - My take: PLTR had strong momentum (+6.8). Stonks is right on this one.
- Read Aldridge's journal: "No trades today. Market overvalued."
  - Aldridge is always cautious. Not actionable.
- News scan: NVDA analyst upgrade (target $250 from $180). Confirms hold.
- Performance review: -$26.33 unrealized. SOFI cut was correct. No new entries.
- Tomorrow's plan: If SPY bounces off lower BB, go slightly aggressive.
  If it breaks below, go fully defensive. Key levels: SPY $745 support.

### Committed: a1b2c3d
- strategy.md: tightened stops (5% → 3%) due to CHOPPY regime
- params.json: stop_loss_pct: 3, max_cash_pct: 0.3
```

### Structured Journal (PostgreSQL)

The agent also calls `record_journal()` at key moments:

```json
{
  "trader_id": "kairos",
  "entry_type": "reflection",
  "title": "SOFI cut — lesson learned",
  "body": "SOFI hit watchlist at 14:23. Should have cut then. Waited until close. Lost -3.55%. Rule: if a position hits the watchlist on two consecutive ticks, cut immediately.",
  "metadata": {
    "ticker": "SOFI",
    "action": "sell",
    "pnl_impact": -17.43,
    "git_commit": "a1b2c3d",
    "strategy_version": "v3",
    "params": {
      "stop_loss_pct": 3,
      "max_cash_pct": 0.3
    }
  },
  "created_at": "2026-07-16T15:51:00Z"
}
```

---

## 4. The Strategy File

Every agent has a `strategy.md` file in their workspace that they read at the start of every trading tick. This is their current playbook:

```markdown
# Kairos — Strategy v3 (as of commit a1b2c3d)

## Current Thesis
Market is CHOPPY (3 consecutive days). SPY RSI oscillating 49-55.
No clear direction. Tech sector (QQQ -2%) underperforming defensives (WMT +2.5%).

## Approach
- Defensive posture: max 30% deployed
- Tight stops: 3% (reduced from 5%)
- Only enter on confirmed bullish divergence (MACD + rising RSI)
- Cut any position below mid-BB with declining momentum

## Key Levels
- SPY $745 → strong support, bounce = buy signal
- SPY $740 → break = go fully defensive (10% max)
- QQQ $695 → oversold, watch for reversal

## Watchlist
- NVDA: bullish MACD divergence, hold
- AMZN: similar pattern to NVDA, hold
- WMT: strong defense rotation, hold (small)
- SOFI: cut, no re-entry until student loan clarity

## ML Models
- Regime: HMM v2 (trained 2026-07-15, accuracy 72%)
- No GPU inference needed today — regime is stable
```

---

## 5. The Learning Feedback Loop

### Short-Term Learning (per tick)

```
Trade → Record decision → Write reflection → Read next tick → Apply lesson
```

A trader who cuts SOFI learns "cut faster" and applies it on the next position. This happens within minutes.

### Medium-Term Learning (per night)

```
Nightly maintenance:
  → Read own journal from today
  → Read other traders' journals
  → Read news
  → Form hypotheses
  → Propose strategy changes
  → Commit changes via git
  → Update PG params
```

A trader who sees Stonks profiting on sentiment-driven trades might adjust their own sentiment weighting.

### Compressed-Time Learning (during backtests)

The learning loops run at **accelerated speed** during historical backtests. This is how the system makes up for missed time — it simulates weeks of trading in hours, with the full learning loop at every step.

#### Per Virtual Trade (short loop, compressed)

```
Backtest trade fills → Record virtual decision → Write reflection →
Update strategy hypothesis → Next virtual trade (seconds later)

Same loop as live trading, but compressed: 100 trades in 5 minutes
instead of 100 trades in 5 days.
```

#### Per Virtual Day (medium loop, compressed)

```
Backtest day ends (virtual 16:00 ET) →
→ Read virtual journal from "today"
→ Read other traders' virtual journals
→ Form hypotheses from today's simulated results
→ Propose strategy changes
→ Commit changes via git
→ Update params for next virtual day
→ Advance to next virtual trading day (seconds later)

Same loop as nightly maintenance, but compressed:
30 virtual days in 15 minutes instead of 30 real days.
```

#### The Goal: Compressed Experience

```
Real Time     Virtual Time     Learning Cycles
─────────────────────────────────────────────────────
1 night       3 months          ~60 virtual days
                                 ~600 virtual trades
                                 60 medium-loop runs
                                 600 short-loop runs

By market open: the agent has "lived" 3 months of trading
and iterated on their strategy 60+ times.
```

This is how the system makes up for months of lost time in a single night. The agent emerges the next morning with months of simulated experience and an evolved strategy.

#### How It Works in the Terminal

```
Terminal.get_mode() → "backtest"
  → submit_order() → sim engine (no real Alpaca call)
  → record_journal() → writes to backtest's simulated context
  → set_mode({date: "2026-07-20"}) → advance one virtual day
  → agent runs nightly maintenance loop
  → set_mode({date: "2026-07-21"}) → next virtual day
  → ... repeat for N virtual days
```

The Terminal doesn't distinguish between live and simulated journals — they both land in the same format. The only difference is the `is_backtest` flag.

#### Learning Acceleration

| Scenario | Real Time | Virtual Time | Learning Cycles |
|----------|-----------|--------------|-----------------|
| Live trading only | 1 week | 1 week | 5 medium loops |
| Live + nightly GPU | 1 week | 1 week + param opt | 5 + GPU runs |
| Live + nightly backtest | 1 night | 3 months | 60+ medium loops |
| Full backtest campaign | 1 weekend | 2 years | 500+ medium loops |

#### Baked Into Phase 1

The compressed learning loop runs from Phase 1 — the backtest engine is local (SQLite), same params.json + strategy.md + journal.md flow, just accelerated. No GPU needed.

### Long-Term Learning (per week/month)

```
GPU parameter optimization:
  → Run 10,000+ backtest combinations
  → Find optimal params for current regime
  → Deploy best params
  → Track performance vs previous params
  → If new params underperform, roll back via git revert
```

The system is constantly asking: "What worked? What didn't? What should I try next?"

---

## 6. Git as the Audit Trail

### Commit Convention

```
<trader>: <change made>

<Rationale: why this change was made>
<Parameters: what params are in effect>

Journal:
<One-line summary of today's results>

Commit: <previous commit hash>
```

Examples:

```
kairos: tighten stop_loss 5%→3% in CHOPPY regime

Rationale: Choppy market detected for 3 sessions. Tighter stops prevent
whipsaw damage. Previous 5% was too loose — positions got cut at
unnecessary losses during intraday swings.

Parameters: stop_loss=3%, max_cash=30%, max_positions=5, hmm_model=v2

Journal: -$26.33 today. Cut SOFI at -3.55% (should have cut sooner).
Holding NVDA (bullish MACD divergence), AMZN (similar), PLTR (strong
momentum). Learning: act on watchlist alerts faster.

Commit: a1b2c3d
```

### Performance Correlation

Over time, you can run queries like:

```sql
-- Which commits improved performance?
SELECT 
  j.metadata->>'git_commit' as commit,
  j.metadata->>'strategy_version' as version,
  AVG(pnl_impact) as avg_pnl,
  COUNT(*) as trades,
  SUM(CASE WHEN pnl_impact > 0 THEN 1 ELSE 0 END)::float / COUNT(*) as win_rate
FROM trading.journals j
WHERE j.metadata->>'git_commit' IS NOT NULL
GROUP BY commit, version
ORDER BY avg_pnl DESC;
```

Or in git:

```bash
# Show performance per commit
for commit in $(git log --oneline --all -- kairos/params.json | awk '{print $1}'); do
    echo "Commit: $commit"
    git show $commit:kairos/params.json | python -m json.tool
    echo "---"
done
```

### Rollback

If a strategy change underperforms:

```bash
git revert abc123
git push origin main
# Next tick: agent reads reverted strategy.md
# Next maintenance: agent notes the revert in journal
```

No manual file editing. No state drift. Git is the truth for prompts and params.

---

## 7. The LLM Learning Engine

### What LLMs Do Well

| Capability | How Agents Use It | Example |
|-----------|-------------------|---------|
| **Reflection** | Analyze own trades, extract lessons | "I cut SOFI too late. Rule: act on 2nd watchlist alert." |
| **Cross-pollination** | Read other traders' journals, form opinions | "Stonks is bullish on PLTR. My NVDA has similar momentum. Confirmed." |
| **Hypothesis formation** | Generate new strategy ideas from patterns | "NVDA bounced off lower BB 3 times this month. If it dips again, buy." |
| **Narrative construction** | Explain market context in journal | "Tech selloff driven by macro fears, not fundamentals. Temporary." |

### What LLMs Don't Do Well (Use GPU Instead)

| Capability | What to Use | Example |
|-----------|-------------|---------|
| **Parameter sweep** | GPU (param optimizer) | "Find best RSI period for current regime" |
| **Signal weighting** | GPU (signal weighter) | "Learn which signals predict my wins" |
| **Numerical optimization** | GPU (HMM trainer) | "Train HMM on 2 years of SPY data" |
| **Correlation analysis** | GPU (inference) | "How correlated are my positions?" |

### The Division of Labor

```
Agent (LLM)                        GPU (ML)
    │                                  │
    │ "I think tighter stops           │
    │  would work better"              │
    │                                  │
    │←── proposal ──────────────────── │
    │                                  │
    │ "Test it: run 5000               │
    │  backtest combinations"          │
    │─────────────────── request ──→   │
    │                                  │
    │←── results: optimal = 3.2% ──── │
    │                                  │
    │ "Good. I'll commit               │
    │  stop_loss=3.2%"                 │
    │                                  │
    │── git commit ──────────────────→ │
```

The LLM forms hypotheses. The GPU tests them. The agent commits the winner. The system learns.

---

## 8. The Nightly Learning Schedule

| Time (ET) | Activity | Who | Duration |
|-----------|----------|-----|----------|
| 16:00 | Market closes | — | — |
| 16:30-17:00 | Kairos: nightly maintenance (journal, trim, read, reflect) | Isolated cron | 30 min |
| 16:35-17:05 | Stonks: nightly maintenance | Isolated cron | 30 min |
| 16:40-17:10 | Aldridge: nightly maintenance | Isolated cron | 30 min |
| 17:00-20:00 | Free time (optional: extended reflection, cross-pollination) | — | 3h |
| 00:00-02:00 | GPU parameter optimization across all traders | Isolated cron | 2h |
| 02:00-09:30 | Backfill historical data, idle | Background | — |
| 09:30 | Market opens | — | — |

### What Each Stage Learns

| Stage | Learning Output | Where It Goes |
|-------|----------------|---------------|
| Nightly maintenance | Reflection entries, strategy proposals | PG.journals, git commits |
| Cross-reading | Cross-trader opinions | PG.journals, local notes |
| GPU optimization | Optimal params per trader | PG.params, params.json |
| Backfill | More historical data | PG.historical_ticks |

By market open, every agent has:
- Reflected on yesterday's performance
- Read what the other agents did
- Formed opinions about the current market
- A fresh strategy.md and params.json
- A full night of GPU-optimized parameters

---

## 9. Virtual Agents for Faster Learning

### The Multi-Agent Experiment

```
Same strategy, different params
│
├── kairos-aggressive: stop_loss=5%, max_cash=90%, rsi_period=10
├── kairos-defensive:  stop_loss=3%, max_cash=30%, rsi_period=20
├── kairos-balanced:   stop_loss=4%, max_cash=60%, rsi_period=14
└── kairos-ml-opt:     (params from last GPU run)
```

All four run the same strategy with different parameter sets. The leaderboard shows all four. Over time, the best performer's params get adopted as the default.

This works during **backtest mode** (nighttime) or **live at small sizes** (daytime). The key principle: more experiments = more data = faster learning.

### Experiment Lifecycle

```
1. Agent proposes experiment:
   "I want to try RSI period = 10 for 5 days"
   
2. System creates virtual agent:
   - Copies strategy.md
   - Sets params.json with RSI=10
   - Registers as kairos-rsi10
   - Starts trading (backtest or live with small capital)

3. After 5 days, compare:
   - kairos-rsi10: +3.2%, Sharpe 1.4, 12 trades
   - kairos-default: +1.8%, Sharpe 0.9, 8 trades

4. If winner: adopt params. Commit the change.
   If loser: discard. Write reflection on why it failed.
```

---

## 10. How Learning Compounds Over Time

### Month 1

- Agents trade with basic RSI/MACD signals
- No ML models yet
- Journals are verbose, lessons are obvious
- Win rate: ~45%

### Month 2

- First HMM regime model trained on GPU
- Agents adjust posture based on regime signal
- Params start getting tuned per regime
- Win rate: ~48%

### Month 3

- Signal weighting deployed — agents know which signals matter
- Parameter optimization runs nightly
- Virtual agents explore strategy variants
- Win rate: ~52%

### Month 6

- Accumulated 6 months of 5-min bars in historical_ticks
- Backtests are accurate and meaningful
- Multiple ML models deployed (regime, signal weights, param opt)
- Win rate: ~55%
- Drawdowns: smaller due to tightened params

### Year 1

- Full year of historical data available
- ML models trained on a full market cycle (bull, bear, choppy)
- Agents adapt to regime changes within days
- System runs autonomously — you just review journals
- Win rate: ~58-60% (realistic target for systematic strategies)

### The Trajectory

```
Learning Rate Over Time

Performance
    ↑
60% ┤                          ╱╲
55% ┤                      ╱╱  ╲╲
50% ┤                  ╱╱       ╲╲
45% ┤             ╱╱              ╲╲
40% ┤        ╱╱                      ╲╲
35% ┤   ╱╱                              ╲╲
    └──────────────────────────────────────────→ Time
       M1     M2     M3     M6            Y1

Key: steep initial learning (agents learn basic rules), plateau (system
stabilizes), gradual improvement (ML models accumulate data and accuracy)
```

The system never stops learning. Every trade is a data point. Every night is a training session. Every commit is a hypothesis test.

---

## Summary: The Complete Learning Flow

```
MARKET HOURS
┌─────────────────────────────────────────────────────────────────┐
│ tick_cron dispatches → agent reads strategy.md + params.json    │
│                      → Terminal.get_quotes()                    │
│                      → agent analyzes, forms thesis             │
│                      → Terminal.submit_order() or hold           │
│                      → agent writes local journal.md             │
│                        (with git commit, params, reflection)     │
│                      → Terminal.record_decision()                │
│                      → Terminal.record_journal()                 │
└─────────────────────────────────────────────────────────────────┘

NIGHTLY MAINTENANCE
┌─────────────────────────────────────────────────────────────────┐
│ cron fires → agent reads own journal from today                  │
│            → Terminal.get_journals() for other traders           │
│            → Terminal.get_news()                                 │
│            → agent reflects, forms hypotheses                    │
│            → updates strategy.md and params.json                 │
│            → git commit with full rationale and params           │
│            → Terminal.set_params() to update PG                  │
│            → Terminal.record_journal({type: "summary"})          │
└─────────────────────────────────────────────────────────────────┘

GPU OPTIMIZATION (MIDNIGHT)
┌─────────────────────────────────────────────────────────────────┐
│ cron fires → Terminal.get_historical_data() for recent period    │
│            → Terminal.submit_train_job({param_optimizer})        │
│            → GPU runs 10,000+ combinations                       │
│            → Results stored in ml_jobs table                     │
│            → Best params deployed to PG.params                   │
│            → Next tick: agent reads updated params.json          │
└─────────────────────────────────────────────────────────────────┘

THE LOOP COMPLETE. NEXT DAY BEGINS WITH IMPROVED PARAMS.
```

---

*This is a living document. Every section is up for debate. Tear it apart.*