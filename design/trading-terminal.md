# Trading Terminal — MCP Server Design

> **Status**: Draft v2 (updated w/ Hermes intel) | **Date**: 2026-07-16
> **Board**: `trading-terminal` workboard | **PR**: [#1](https://github.com/Tesselation-Studios/paper-trading-agents/pull/1)

---

## 1. Current State — What Actually Exists

```
┌─────────────────────────────────────┐
│ .41 VM (paper-trading-rebuild)      │  ← spinning HDD, slow I/O
│                                     │
│  tick_cron.py (runs)                │
│  → fetches snapshots                │
│  → dispatches OpenClaw agents       │
│  → writes tick_queue                │
│                                     │
│  LLM Engine (hermes-trader, runs)   │
│  → decisions → PG ✅                │
│  → journal → PG ✅                  │
│                                     │
│  sync_alpaca_positions (5 min cr.)  │
│  → positions → PG ✅                │
│  → portfolio snaps → PG ✅          │
│                                     │
│  .41:5000 — old data bus (has LLM)  │
│  .41:5000 — trade exec: ❌ MISSING  │
└────────────┬────────────────────────┘
             │
┌────────────▼────────────────────────┐
│ docker.klo (.179, 20c/32GB/4TB+SSD) │
│                                     │
│  trading-db (PostgreSQL)            │
│  ← all writes land here ✅          │
│                                     │
│  trading-data-bus (.179:5000)       │
│  → quotes, social, indicators       │
│                                     │
│  trading-leaderboard (.179:5002)    │
│  → reads PG for rankings            │
│                                     │
│  trading-dashboard (.179:5004)      │
│  → trading.wodinga.studio (healthy) │
└─────────────────────────────────────┘
```

### What Works
- **Decisions, journal, portfolio snapshots, positions** all flowing to PG ✅
- **Dashboard healthy** at trading.wodinga.studio ✅
- **tick_cron** fixed (URL + tick_id) ✅
- **Sync positions cron** running every 5 min ✅
- **Trader model** → Gemini 3.5 Flash (cheap) ✅

### What's Broken
1. **Trade execution is missing** — decisions get logged but no orders get placed in Alpaca → no trades in DB since July 10
2. **Two data busses** — .41:5000 (has LLM) vs .179:5000 (pure data). Should consolidate.
3. **OpenClaw agents dispatch but output doesn't flow** — sync_agents_to_pg.py cron exists but never ran.
4. **Historical trader needs polish** — replay harness, learning loop signals, param optimization.
5. **Signals table stale** — `trading.signants` frozen July 8, dashboard falls back to `trading.decisions`.

### Root Cause

The .41 VM runs on a spinning HDD. Disk I/O throttles everything — agent thinking, SQLite writes, Docker operations, everything. Docker.klo has 20 cores, 32 GB RAM, 4 TB+ SSD — that's where the pipeline needs to live.

---

## 2. Solution: The Trading Terminal

A **single authenticated MCP server** on docker.klo that replaces the current split-brain architecture with one unified pipeline:

```
fetch data → LLM decides → execute order → write everything to PG
```

### Core Principles

| Principle | Why |
|-----------|-----|
| **One service** | One deploy, one set of Alpaca keys, one auth model, one thing to debug |
| **Agents don't touch Alpaca directly** | Keys live in the Terminal. Agents present their own API key. Security + simplicity. |
| **Share everything** | News, signals, historical data — all in one PG. Trader A's research feeds Trader B. |
| **Archive forever** | Every quote fetched gets stored. Over time, a rich historical corpus for backtesting. |
| **Auth per trader, data per trader** | API key scopes portfolio/orders to that trader. Shared data (macro, news, signals) is accessible by all. |
| **Same schema for live + historical** | Backtests use the same tables with an `is_backtest` flag. Leaderboard shows both. |

### What It Replaces

| Old Thing | Replaced By |
|-----------|-------------|
| .41:5000 data bus | Terminal MCP tools |
| .179:5000 data bus | Terminal MCP tools |
| skill_alpaca.py (3 copies) | Terminal `submit_order()` tool |
| execute.py | Terminal internal execution engine |
| sync_alpaca_positions.py | Terminal writes positions on every order |
| sync_agents_to_pg.py | Terminal writes everything transactionally |
| SQLite journal files | Terminal `record_journal()` → PG |
| (nothing — new) | Historical accumulator + backtest engine |
| (nothing — new) | Background news poller + archive |

---

## 3. Architecture

```
                   ┌─────────────────────────────────────────────────┐
                   │           docker.klo (20c/32GB/4TB)             │
                   │                                                 │
                   │  ┌─────────────────────────────────────────┐   │
                   │  │          Trading Terminal (MCP)          │   │
                   │  │  Python + FastMCP / MCP SDK              │   │
                   │  │  Port: 5001 (SSE transport)              │   │
                   │  │                                           │   │
                   │  │  ┌─────────────────────────────────────┐ │   │
                   │  │  │  Auth Layer                         │ │   │
                   │  │  │  → API key → trader identity       │ │   │
                   │  │  │  → Scopes all downstream calls     │ │   │
                   │  │  └─────────────────────────────────────┘ │   │
                   │  │                                           │   │
                   │  │  ┌─────────────────────────────────────┐ │   │
                   │  │  │  MCP Tools                          │ │   │
                   │  │  │  → Data tools (shared)              │ │   │
                   │  │  │  → Trading tools (scoped)           │ │   │
                   │  │  │  → Historical tools (shared)        │ │   │
                   │  │  └─────────────────────────────────────┘ │   │
                   │  │                                           │   │
                   │  │  ┌─────────────────────────────────────┐ │   │
                   │  │  │  Execution Engine                   │ │   │
                   │  │  │  → Live mode: Alpaca API            │ │   │
                   │  │  │  → Backtest mode: sim engine        │ │   │
                   │  │  │  → Both write to same PG tables     │ │   │
                   │  │  └─────────────────────────────────────┘ │   │
                   │  └─────────────────────────────────────────┘   │
                   │                                                 │
                   │  Background Workers:                            │
                   │  ┌─────────────────────────────────────────┐   │
                   │  │  News Poller                            │   │
                   │  │  • Rate-limited, round-robin sources    │   │
                   │  │  • Stores in news_archive (deduped)     │   │
                   │  │  • Priority queue from watch_symbol()   │   │
                   │  │                                          │   │
                   │  │  Data Refresher                          │   │
                   │  │  • Pre-fetches quotes/flow/macro/signal  │   │
                   │  │  • In-memory cache + PG persistence      │   │
                   │  │  • Adjusts cadence by market hours       │   │
                   │  │                                          │   │
                   │  │  Historical Accumulator                  │   │
                   │  │  • Every quote fetch → OHLCV to PG       │   │
                   │  │  • Builds growing corpus for backtests   │   │
                   │  │  • Escapes Alpaca's 5-day window limit   │   │
                   │  └─────────────────────────────────────────┘   │
                   │                                                 │
                   │  ┌─────────────────────────────────────────┐   │
                   │  │  PostgreSQL (shared DB)                  │   │
                   │  │  Persists: positions, orders, journals,  │   │
                   │  │  decisions, news_archive, historical_    │   │
                   │  │  ticks, signals, watchlist               │   │
                   │  └─────────────────────────────────────────┘   │
                   └─────────────────────────────────────────────────┘

┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│  Kairos Agent        │  │  Stonks Agent        │  │  Aldridge Agent      │
│  (key: kairos_key)   │  │  (key: stonks_key)   │  │  (key: aldridge_key) │
│                      │  │                      │  │                      │
│  Connects via MCP    │  │  Connects via MCP    │  │  Connects via MCP    │
│  (SSE)               │  │  (SSE)               │  │  (SSE)               │
└──────────────────────┘  └──────────────────────┘  └──────────────────────┘

┌──────────────────────────────────────┐
│  Leaderboard UI (trading.wodinga.x)  │
│  → reads PG directly (no API dep)   │
│  → shows live AND historical results │
└──────────────────────────────────────┘
```

---

## 4. Auth & Security

### Per-Trader API Keys

Each agent gets a unique key stored in OpenClaw's env:

```
TRADING_TERMINAL_KEY_KAIROS=tt_xxxxx
TRADING_TERMINAL_KEY_STONKS=tt_yyyyy
TRADING_TERMINAL_KEY_ALDRIDGE=tt_zzzzz
```

The Terminal resolves the key to a trader identity and scopes all calls:
- `get_portfolio()` → only that trader's portfolio
- `submit_order()` → places from that trader's Alpaca account
- `record_journal()` → writes with that trader's ID
- `get_news()`, `get_macro()` → shared, accessible by all

### Alpaca Keys

**Alpaca keys live ONLY in the Terminal's environment** on docker.klo. Never transmitted. Never seen by agents.

```
ALPACA_KAIROS_KEY=***
ALPACA_KAIROS_SECRET=***
ALPACA_STONKS_KEY=***
ALPACA_STONKS_SECRET=***
ALPACA_ALDRIDGE_KEY=***
ALPACA_ALDRIDGE_SECRET=***
```

### Threat Model

| Threat | Mitigation |
|--------|-----------|
| Agent key leaks | Key rotation. Keys are bearer tokens scoped per trader. |
| Agent impersonates another trader | Key scoping. Terminal rejects mismatched trader_id. |
| Alpaca key leak | Never leave docker.klo environment. Agents never see them. |
| Unauthenticated requests | Every MCP tool call requires valid key in header/auth. |

---

## 5. MCP Tool Definitions

### Data Tools (shared across all traders)

| Tool | Params | Returns | Cache TTL |
|------|--------|---------|-----------|
| `get_quotes` | `symbols: string[]` | OHLCV + RSI per symbol | 5s |
| `get_macro` | — | FOMC, yield curve, GDP, CPI, unemployment | 1h |
| `get_sentiment` | `symbol: string` | FinBERT + Praesentire scores | 5m |
| `get_sentiment_divergence` | `symbol: string` | EN vs ZH sentiment + divergence score | 10m |
| `get_flow` | `symbol: string` | Unusual options flow | 5m |
| `get_insiders` | `symbol: string` | SEC Form 4 filings | 30m |
| `get_technical_scan` | `symbol: string` | Multi-TF RSI/MACD/BB (15m/1h/4h/1d) | 5m |
| `get_risk` | `symbol: string` | VaR, beta, correlation, concentration | 15m |
| `get_market_regime` | — | ML regime signal | heartbeat |
| `get_news` | `filters: {symbol?, sources?, categories?, since?, limit?}` | Archived news entries | instant |
| `get_leaderboard` | — | All trader P&L, positions, rankings | 5s |

### Trading Tools (scoped by API key)

| Tool | Params | Returns | Notes |
|------|--------|---------|-------|
| `get_portfolio` | — | `{value, cash, positions[], P&L}` | Scoped to trader |
| `get_positions` | — | Open positions with P&L | Scoped to trader |
| `submit_order` | `{symbol, qty, side, type, time_in_force}` | Order confirmation | Validates via entry gate |
| `cancel_order` | `order_id: string` | Cancellation confirmation | — |
| `get_orders` | `status?, limit?` | Order history | Scoped to trader |
| `record_journal` | `{entry_type, title, body, metadata?}` | Journal record ID | Structured + append-only |
| `record_decision` | `{ticker, action, rationale, confidence?}` | Decision record ID | For leaderboard/audit |
| `watch_symbol` | `symbol: string, priority?: int` | Confirmation | Adds to news poller queue |

### Historical/Backtest Tools

| Tool | Params | Returns | Notes |
|------|--------|---------|-------|
| `get_historical_data` | `{symbol, start, end, interval?}` | OHLCV bars | From accumulated historical_ticks |
| `set_mode` | `mode: "live" \| "backtest"` | Mode confirmed | Switches execution engine |
| `get_mode` | — | Current mode | Status check |

---

## 6. Pipeline Flow

### Live Trading (market hours)

```
tick_cron.py (.41)
  → dispatches agent (Kairos/Stonks/Aldridge)
  → agent connects to Terminal
  → Terminal.get_quotes(symbols)  ← cache hit or Alpaca fetch
  → agent analyzes
  → Terminal.record_decision(...)  ← writes to PG
  → Terminal.submit_order(...)     ← hits Alpaca API
  → Terminal writes order + position to PG
  → Terminal.record_journal(...)   ← writes to PG
  → Leaderboard reads from PG ← live updated
```

### Historical Trading (off hours / backtest)

```
Agent (any trader)
  → Terminal.set_mode("backtest", {date: "2026-07-15"})
  → Terminal.get_quotes("NVDA")    ← from historical_ticks table
  → agent analyzes
  → Terminal.record_decision(...)   ← same PG, is_backtest=true
  → Terminal.submit_order(...)      ← sim engine, same PG tables
  → Terminal.record_journal(...)    ← same PG, is_backtest=true
  → Leaderboard shows backtest results tagged
```

---

## 7. Database Schema

The Terminal writes to the **existing `trading` schema** in PG on docker.klo. New tables are added, existing tables are adapted.

### Existing Tables (already in PG)

| Table | Status | Notes |
|-------|--------|-------|
| `trading.decisions` | ✅ Flowing | Already written by LLM engine |
| `trading.trader_journals` | ✅ Flowing | Already written by LLM engine |
| `trading.portfolio_snapshots` | ✅ Flowing | Already written by sync script |
| `trading.positions` | ✅ Flowing | Already written by sync script |
| `trading.signants` (signals) | ❌ Stale | Frozen July 8 — needs replacement |

### New Tables (need creation)

```sql
-- Orders (execution records — currently MISSING from pipeline)
CREATE TABLE trading.orders (
    id TEXT PRIMARY KEY,                   -- Alpaca order ID or local UUID
    trader_id TEXT NOT NULL,               -- 'kairos', 'stonks', 'aldridge'
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,                    -- 'buy', 'sell'
    order_type TEXT NOT NULL,              -- 'market', 'limit', 'stop', 'stop_limit'
    qty NUMERIC NOT NULL,
    status TEXT NOT NULL,                  -- 'new', 'filled', 'partially_filled', 'canceled', 'rejected'
    filled_qty NUMERIC DEFAULT 0,
    filled_avg_price NUMERIC,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    filled_at TIMESTAMPTZ,
    is_backtest BOOLEAN DEFAULT FALSE,
    backtest_run_id TEXT,                  -- NULL for live trades
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Historical ticks (Alpaca OHLCV accumulation)
CREATE TABLE trading.historical_ticks (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,                -- '1Min', '5Min', '15Min', '1D'
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

-- News archive
CREATE TABLE trading.news_archive (
    id BIGSERIAL PRIMARY KEY,
    symbols TEXT[],                        -- related tickers
    title TEXT NOT NULL,
    body TEXT,
    source TEXT NOT NULL,                  -- 'alpaca', 'reddit', 'stocktwits', 'bluesky'
    url TEXT,
    relevance_score NUMERIC,
    categories TEXT[],                     -- ['earnings', 'macro', 'regulatory', ...]
    published_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(url, published_at)
);

-- Watchlist (news poller priority queue)
CREATE TABLE trading.watchlist (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    requested_by TEXT,                     -- trader_id or 'auto'
    priority INTEGER DEFAULT 5,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_polled_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE
);

-- Trader API keys
CREATE TABLE trading.trader_api_keys (
    id BIGSERIAL PRIMARY KEY,
    trader_id TEXT NOT NULL UNIQUE,
    key_hash TEXT NOT NULL,                -- bcrypt or similar
    label TEXT,                            -- human-readable
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

-- Signals (replaces stale trading.signants)
CREATE TABLE trading.signals_v2 (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,                  -- 'kairos', 'stonks', 'aldridge', 'terminal'
    ticker TEXT NOT NULL,
    signal_type TEXT NOT NULL,             -- 'momentum', 'value', 'sentiment', 'flow', 'insider'
    direction TEXT NOT NULL,               -- 'bullish', 'bearish', 'neutral'
    strength NUMERIC,                      -- 0.0 to 1.0
    rationale TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Backtest runs
CREATE TABLE trading.backtest_runs (
    id TEXT PRIMARY KEY,                   -- UUID
    trader_id TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'backtest',
    symbols TEXT[],                        -- symbols traded
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
```

---

## 8. Agent Onboarding

### What Changes

| Current | After Migration |
|---------|----------------|
| Local curl to .41:5000 or .179:5000 | MCP tools via `trading-terminal` server |
| skill_alpaca.py for orders | `submit_order()` tool |
| Manual markdown journals | `record_journal()` for structured entries |
| Local append-only logs **still kept** | Local logs remain for stream-of-consciousness |
| Can't see other traders' signals | Shared `get_signals()` from Terminal |

### Local Append-Only Logs

Agents still maintain local markdown files for their own reasoning:
```
kairos/journal/2026-07-16.md  ← raw thoughts, rejected ideas, full reasoning
```

Structured decisions go through `record_journal()` → PG → leaderboard. Two fidelity levels:
- **Local**: high-fidelity, stream-of-consciousness, complete reasoning chain
- **Terminal**: structured, auditable, leaderboard-visible

### MCP Client Config (OpenClaw)

In each trader's config:

```json
{
  "mcpServers": {
    "trading-terminal": {
      "transport": "sse",
      "url": "http://docker.klo:5001/sse",
      "env": {
        "TRADING_TERMINAL_KEY": "${TRADING_TERMINAL_KEY_KAIROS}"
      }
    }
  }
}
```

---

## 9. Historical & Backtest Engine

### Mode System

```
mode = "live"     → submit_order() hits Alpaca API
mode = "backtest" → submit_order() hits internal sim engine
```

The `set_mode()` tool returns the current mode and any active backtest context.

### Backtest Engine Behavior

1. `set_mode("backtest", date="2026-07-15")`
   - Creates a `backtest_runs` record
   - Simulated portfolio initialized with `initial_capital` (configurable)
2. `get_portfolio()` returns simulated portfolio
3. `get_quotes("NVDA")` returns bars from `historical_ticks` filtered to date range
4. `submit_order(...)` simulates fill:
   - Looks up matching bar in historical_ticks
   - Fills at bar open (default) or configurable fill model
   - Updates simulated position/portfolio
5. On completion: backtest stats (return, sharpe, max drawdown) written to `backtest_runs`

### Data Growth

| Time | Historical Data Available |
|------|--------------------------|
| Day 1 | 5 trading days (Alpaca window) |
| Week 2 | ~12 trading days |
| Month 1 | ~25 trading days |
| Month 3 | ~65 trading days |
| Year 1 | ~260 trading days |

More data = more accurate backtests = better agent models.

---

## 10. News Aggregation

### Background Poller

```
loop:
  for each source in [Alpaca News, Reddit, Stocktwits, Bluesky]:
    for each active symbol in watchlist (sorted by priority desc):
      fetch news for symbol from source
      deduplicate by (url, published_at)
      upsert into news_archive
      update watchlist.last_polled_at
      sleep(source_rate_limit)
```

### Rate Limits

| Source | Rate Limit | Strategy |
|--------|-----------|----------|
| Alpaca News | 10 req/min | Primary source, bulk unfiltered |
| Reddit | Varies | Per-subreddit, rotating accounts |
| Stocktwits | Varies | Public API |
| Bluesky | Varies | AT Protocol |

### Shared Archive

If Kairos watches `NVDA`:
1. News poller adds NVDA to rotation
2. News fetched → stored in `news_archive`
3. Stonks calls `get_news(symbol="NVDA")` → returns from archive, no new fetch
4. Aldridge calls `get_news(symbol="NVDA", categories=["macro"])` → filtered from same archive

The more traders use the system, the less redundant fetching happens.

---

## 11. Deployment

```yaml
# docker-compose.yml
services:
  terminal:
    build: .
    ports:
      - "5001:5001"
    env_file: .env          # Alpaca keys + DB creds
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5001/health"]
      interval: 30s

  db:
    image: postgres:16
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: trading
      POSTGRES_USER: terminal
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
    secrets:
      - db_password
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U terminal -d trading"]
      interval: 10s
    restart: unless-stopped

volumes:
  pgdata:
```

Deploy via scp + ssh for now. GitHub Actions later.

---

## 12. Migration Phases

| Phase | What | Duration |
|-------|------|----------|
| **1** | Build Terminal MCP server with core tools | Build session |
| **2** | Deploy to docker.klo, connect to existing PG | Build session |
| **3** | Switch Stonks (simplest trader) to Terminal-only | Day 1 |
| **4** | Verify: leaderboard, orders, journals all flow | Day 1-2 |
| **5** | Switch Kairos + Aldridge | Day 2-3 |
| **6** | Deprecate .41:5000 data bus + old sync scripts | Day 3-4 |
| **7** | Backfill historical_ticks from existing data | Ongoing |

---

*This is a living document. Every section is up for debate. Tear it apart.*
