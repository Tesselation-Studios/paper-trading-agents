# Migration Plan — Trading Terminal Cutover

> **Status**: Draft | **Date**: 2026-07-16
> **Parent**: [trading-terminal.md](trading-terminal.md) — Design doc
> **Board**: `trading-terminal` workboard

## Overview

This document covers the phased migration from the current split-brain architecture to the unified Trading Terminal. It's the "how" — the design doc covers the "what."

---

## Phase 0: Pre-Migration Inventory

Before touching anything, document what exists:

| Component | Location | Status | To Do |
|-----------|----------|--------|-------|
| .41:5000 data bus | .41 VM | Running | Keep running until terminal is stable |
| .179:5000 data bus | docker.klo | Running | Keep running until terminal is stable |
| skill_alpaca.py (3 copies) | .41 VM | In use | Replace with Terminal submit_order() |
| execute.py | .41 VM | In use | Replace with Terminal execution engine |
| sync_alpaca_positions.py | .41 VM | Running | Replace with Terminal real-time writes |
| sync_agents_to_pg.py | .41 VM | Exists, never ran | Remove — Terminal handles this |
| sync_decisions_to_pg.py | .41 VM | Missing | Remove — Terminal handles this |
| tick_cron.py | .41 VM | Running | Keep — dispatches agents |
| LLM Engine (hermes-trader) | .41 VM | Running | Keep — feeds data to Terminal |
| trading-db (PG) | docker.klo | Running | Terminal connects to existing PG |
| trading-leaderboard | docker.klo | Running | Reads from PG — no change needed |
| trading-dashboard | docker.klo | Running | No change needed |
| gpu-compute worker | iMac | Running | Terminal wraps calls |

---

## Phase 1: Build & Deploy Terminal

### 1a. Create New Tables

Run migration SQL against the existing PG on docker.klo:

```sql
-- New tables only. Existing tables (decisions, trader_journals,
-- portfolio_snapshots, positions) are LEFT AS-IS.

CREATE TABLE trading.orders (
    id TEXT PRIMARY KEY,
    trader_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    qty NUMERIC NOT NULL,
    status TEXT NOT NULL,
    filled_qty NUMERIC DEFAULT 0,
    filled_avg_price NUMERIC,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    filled_at TIMESTAMPTZ,
    is_backtest BOOLEAN DEFAULT FALSE,
    backtest_run_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE trading.historical_ticks (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume NUMERIC NOT NULL,
    trade_count INTEGER,
    vwap NUMERIC,
    UNIQUE(symbol, interval, timestamp)
);

CREATE TABLE trading.news_archive (
    id BIGSERIAL PRIMARY KEY,
    symbols TEXT[],
    title TEXT NOT NULL,
    body TEXT,
    source TEXT NOT NULL,
    url TEXT,
    relevance_score NUMERIC,
    categories TEXT[],
    published_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(url, published_at)
);

CREATE TABLE trading.watchlist (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    requested_by TEXT,
    priority INTEGER DEFAULT 5,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_polled_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE trading.trader_api_keys (
    id BIGSERIAL PRIMARY KEY,
    trader_id TEXT NOT NULL UNIQUE,
    key_hash TEXT NOT NULL,
    label TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE TABLE trading.signals_v2 (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    ticker TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    strength NUMERIC,
    rationale TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE trading.backtest_runs (
    id TEXT PRIMARY KEY,
    trader_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'backtest',
    symbols TEXT[],
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    initial_capital NUMERIC NOT NULL,
    final_value NUMERIC,
    total_return NUMERIC,
    sharpe_ratio NUMERIC,
    max_drawdown NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE trading.ml_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    worker_id TEXT NOT NULL,
    trader_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    model_type TEXT,
    symbol TEXT,
    params JSONB,
    status TEXT NOT NULL,
    result_json JSONB,
    error TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 1b. Build Terminal Server

1. Create Python project with FastMCP (or MCP SDK)
2. Implement all MCP tools from the design doc
3. Implement background workers (news poller, data refresher, historical accumulator, ML health monitor)
4. Implement GPU compute bridge (gRPC client to iMac)
5. Implement execution engine (Alpaca API for live, sim engine for backtest)
6. Implement auth layer (API key → trader identity scoping)
7. Write tests for each component

### 1c. Deploy to docker.klo

```bash
# Build
docker build -t trading-terminal:latest .

# Save + deploy
docker save trading-terminal:latest | gzip > terminal.tar.gz
scp terminal.tar.gz docker.klo:/docker/terminal/
scp -r secrets/ docker.klo:/docker/terminal/secrets/
scp docker-compose.yml docker.klo:/docker/terminal/
ssh docker.klo "cd /docker/terminal && docker compose up -d"
```

### 1d. Verify Deployment

```bash
# Health check
curl http://docker.klo:5001/health

# Auth check
curl -H "Authorization: Bearer $TERMINAL_KEY" http://docker.klo:5001/api/leaderboard

# Data check
curl -H "Authorization: Bearer $TERMINAL_KEY" http://docker.klo:5001/api/quotes?symbols=SPY,NVDA

# GPU check
curl -H "Authorization: Bearer $TERMINAL_KEY" http://docker.klo:5001/api/gpu/health
```

---

## Phase 2: Agent Migration — Stonks (Day 1)

Stonks is the simplest trader — it's sentiment-driven, fewer moving parts, no ML dependency.

### 2a. Configure MCP Client

Add to Stonks' OpenClaw config:

```json
{
  "mcpServers": {
    "trading-terminal": {
      "transport": "sse",
      "url": "http://docker.klo:5001/sse",
      "env": {
        "TRADING_TERMINAL_KEY": "${TRADING_TERMINAL_KEY_STONKS}"
      }
    }
  }
}
```

### 2b. Update Stonks' TOOLS.md

Replace:
```markdown
### Data Fetching
- `curl localhost:5000/quotes?symbols=...`
- `curl localhost:5000/sentiment?symbol=...`
- `curl localhost:5000/flow?symbol=...`
```

With:
```markdown
### Trading Terminal
All data and trading through the Terminal MCP server:
- `get_quotes(symbols=[...])` — OHLCV + RSI
- `get_sentiment(symbol="...")` — FinBERT score
- `get_flow(symbol="...")` — Options flow
- `submit_order({symbol, qty, side, ...})` — Place trade
- `record_journal({entry_type, title, body})` — Log decision
- `watch_symbol(symbol="...")` — Add to news poller
```

### 2c. Update Stonks' AGENTS.md / Prompt

Change the instructions from:
```
1. Fetch data from the data bus at localhost:5000
2. Analyze with your own tools
3. Execute via skill_alpaca.py
4. Log to journal file
```

To:
```
1. Connect to Trading Terminal (MCP)
2. Fetch data via Terminal tools (get_quotes, get_sentiment, etc.)
3. Analyze with your own tools
4. Execute via Terminal.submit_order()
5. Log via Terminal.record_journal() and Terminal.record_decision()
6. Keep local append-only journal for stream-of-consciousness
```

### 2d. Run Parallel for a Day

- Stonks trades through Terminal ONLY
- Old tools still available as fallback
- Monitor: leaderboard shows correct data, orders flow, journals populate
- Compare: decision quality should be unchanged (same data sources, just through Terminal)

### 2e. Rollback if Needed

```bash
# Remove MCP config from Stonks' openclaw.json
# Restart OpenClaw gateway
# Stonks falls back to old tools
```

---

## Phase 3: Agent Migration — Kairos (Day 2-3)

Kairos is more complex — it uses ML, has a discovery scan, and runs the core loop.

### 3a. Same MCP Config + TOOLS.md Update

Same pattern as Stonks, plus:

### 3b. GPU Compute Migration

**Before:** Kairos calls GPU directly (gRPC to iMac or SSH scripts).

**After:** Kairos calls `submit_train_job()` / `submit_inference_job()` via Terminal.

Update Kairos' prompt:
```
# Old
Run HMM model on SPY data via GPU tool

# New
Submit ML job via Terminal: submit_train_job({model_type: "hmm", symbol: "SPY"})
Poll via Terminal: get_job_status(job_id)
Use result in decision making
```

### 3c. Discovery Scan Migration

**Before:** Kairos runs `scripts/stock_discovery.py` locally.

**After:** Discovery is a Terminal tool or a periodic background worker. If it's an agent decision, the agent calls `get_quotes(watchlist)` and `get_technical_scan(symbol)` through Terminal.

### 3d. Entry Gate Migration

**Before:** Entry gate logic lives in Kairos' prompt or a local script.

**After:** Entry gate is enforced by the Terminal. `submit_order()` validates position size, cash available, max drawdown before submission. Same rules, just enforced server-side.

---

## Phase 4: Agent Migration — Aldridge (Day 2-3)

Aldridge is the simplest — value trader, no ML, no discovery scan. Essentially the same as Stonks.

### 4a. Same MCP Config + TOOLS.md Update

### 4b. Value Screen Migration

**Before:** Aldridge calls `get_quotes()` and `get_fundamentals()` from the data bus.

**After:** Same data through Terminal `get_quotes()`.

---

## Phase 5: Hermes Bot Registration (Day 3)

### 5a. Register via API

```bash
curl -X POST http://docker.klo:5001/api/register \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "hermes-bot", "display_name": "Hermes Bot", "strategy": "record_only"}'
```

### 5b. Update Hermes Bot

- Give it the generated API key
- Update its pipeline to write through Terminal instead of direct DB
- Its existing data in PG remains — leaderboard shows it immediately

---

## Phase 6: Deprecation (Day 3-4)

### 6a. Stop Old Data Bus (.41:5000)

Only after all agents are migrated and verified:
```bash
# Stop the old data bus service on .41
# Remove from systemd / cron / whatever keeps it running
```

### 6b. Stop Old Data Bus (.179:5000)

Same — verify Terminal handles all data requests first.

### 6c. Remove Old Sync Scripts

| Script | Action |
|--------|--------|
| sync_alpaca_positions.py | Remove — Terminal writes positions on every order |
| sync_agents_to_pg.py | Remove — never ran, Terminal handles this |
| sync_decisions_to_pg.py | Remove — Terminal handles this |
| skill_alpaca.py (3 copies) | Remove — replaced by Terminal submit_order() |
| execute.py | Remove — replaced by Terminal execution engine |

### 6d. Remove Old SQLite Tables

After confirming all data is in PG:
```bash
# On .41 VM
rm -f data/*.db data/*.sqlite
```

---

## Phase 7: Backfill (Ongoing)

### 7a. Historical Ticks

First run: fetch all available Alpaca data for all watched symbols:
```bash
# Terminal's historical accumulator runs on startup
# Fetches 5 days of 5-min bars for every symbol in trading.positions
# Then continues on every quote fetch
```

### 7b. Signal Backfill

Migrate old `trading.signants` data to `trading.signals_v2`:
```sql
INSERT INTO trading.signals_v2 (source, ticker, signal_type, direction, strength, rationale, created_at)
SELECT source, ticker, 'legacy', direction, strength, rationale, created_at
FROM trading.signants;
```

### 7c. News Backfill

No backfill needed — news_archive starts empty and grows naturally.

---

## Rollback Plan

### If Terminal is Down

1. Agents detect MCP connection failure (timeout)
2. Agents fall back to emergency mode:
   - **No data, no trading** — safest option
   - OR fall back to old data bus endpoints (if still running)
3. Fix Terminal, redeploy, reconnect
4. Missed data is non-critical — next tick fills it in

### If Terminal is Buggy

1. Revert old data bus + sync scripts (still running if not yet deprecated)
2. Remove MCP config from agent OpenClaw configs
3. Agents resume old workflow
4. Fix Terminal, retest, recut

---

## Verification Checklist

| Check | How | Pass/Fail |
|-------|-----|-----------|
| `get_quotes()` returns data | `curl .../api/quotes?symbols=SPY` | |
| `get_portfolio()` returns scoped data | Compare Kairos vs Stonks results | |
| `submit_order()` places real order | Check Alpaca dashboard | |
| `record_journal()` writes to PG | Check leaderboard UI | |
| `get_gpu_health()` returns worker status | `curl .../api/gpu/health` | |
| `submit_train_job()` runs on iMac | Check GPU worker logs | |
| Leaderboard updates within 5s | Dashboard refresh | |
| All agents connected | `/debug/agents` endpoint | |
| No secrets in agent environments | `env \| grep ALPACA` = empty | |

---

## Timeline

| Day | What | Who |
|-----|------|-----|
| **T+0** | Build + deploy Terminal | Orchestrator |
| **T+1** | Stonks migration + verification | Orchestrator |
| **T+2** | Kairos + Aldridge migration | Orchestrator |
| **T+3** | Hermes registration + deprecation | Orchestrator |
| **T+4+** | Backfill, monitoring, optimization | Automated |

---

*This is a living document. Update as migration progresses.*