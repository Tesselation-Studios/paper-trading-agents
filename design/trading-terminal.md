# Trading Terminal — MCP Server Design

> **Status**: Draft | **Date**: 2026-07-16 | **Author**: Casper
> **Board**: [`trading-terminal` workboard](https://github.com/Tesselation-Studios/paper-trading-agents/issues)

## Problem

We currently have a split-brain architecture:

1. **Data Bus** (Flask, localhost:5000) — fetches quotes, flow, insiders, macro, sentiment
2. **Executor** (skill_alpaca.py, execute.py) — places trades via Alpaca API
3. **SQLite tables** (3 of them) — journals, decisions, positions
4. **Dashboard** reads from PG — but sync scripts were missing, cron jobs silently failing

The VM runs on a spinning HDD. Disk I/O throttles everything — agent thinking, SQLite writes, everything.

Docker.klo has 20 cores, 32 GB RAM, 4 TB+ SSD on TrueNAS. That's where this needs to live.

## Solution: The Trading Terminal

A **single authenticated MCP server** on docker.klo that replaces the data bus + executor + sync scripts + SQLite tables. One service. One key store. One database. Every agent connects to it with their own API key.

### Core Principles

| Principle | Why |
|-----------|-----|
| **One service** | One deploy, one set of Alpaca keys, one auth model, one thing to debug |
| **Agents don't touch Alpaca directly** | Keys live in the Terminal. Agents present their own API key. Security + simplicity. |
| **Share everything** | News, signals, historical data — all in one PG. Trader A's research feeds Trader B. |
| **Archive forever** | Every quote fetched, every trade placed, every journal entry — stored. Over time, a rich historical corpus for backtesting. |
| **Auth per trader, data per trader** | API key scopes portfolio/orders to that trader. Shared data (macro, news, signals) is accessible by all. |

## Architecture

```
                   ┌─────────────────────────────────────────────────┐
                   │           docker.klo (20c/32GB/4TB)             │
                   │                                                 │
                   │  ┌─────────────────────────────────────────┐   │
                   │  │          Trading Terminal (MCP)          │   │
                   │  │  Python + FastMCP / MCP SDK              │   │
                   │  │  Port: e.g. 5001                         │   │
                   │  │  Transport: SSE (for persistent conns)   │   │
                   │  │                                           │   │
                   │  │  Tools (per-trader via API key):          │   │
                   │  │   get_quotes, get_portfolio,             │   │
                   │  │   get_macro, get_sentiment,              │   │
                   │  │   get_flow, get_insiders,                │   │
                   │  │   get_technical_scan, get_risk,          │   │
                   │  │   get_news, watch_symbol,                │   │
                   │  │   submit_order, cancel_order,            │   │
                   │  │   get_positions, get_leaderboard,        │   │
                   │  │   record_journal                          │   │
                   │  └─────────────────┬───────────────────────┘   │
                   │                    │                             │
                   │  ┌─────────────────▼───────────────────────┐   │
                   │  │         PostgreSQL (shared DB)           │   │
                   │  │                                          │   │
                   │  │  Tables:                                 │   │
                   │  │   • traders            — trader profiles │   │
                   │  │   • positions          — open positions  │   │
                   │  │   • orders             — order history   │   │
                   │  │   • journals           — structured logs │   │
                   │  │   • decisions          — trading tick    │   │
                   │  │   • news_archive       — accumulated     │   │
                   │  │   • historical_ticks   — OHLCV archive   │   │
                   │  │   • signals            — all signals     │   │
                   │  │   • watchlist          — shared coverage │   │
                   │  └──────────────────────────────────────────┘   │
                   │                                                 │
                   │  Background Workers:                            │
                   │  ┌─────────────────────────────────────────┐   │
                   │  │  News Poller                            │   │
                   │  │  • Rate-limited per source              │   │
                   │  │  • Round-robins through sources         │   │
                   │  │  • Stores in news_archive, deduplicated │   │
                   │  │  • Priority queue for watched symbols   │   │
                   │  │                                          │   │
                   │  │  Data Refresher                          │   │
                   │  │  • Quotes / flow / macro refresh loop    │   │
                   │  │  • In-memory cache + PG persistence      │   │
                   │  │  • Adjusts cadence by market hours       │   │
                   │  │                                          │   │
                   │  │  Historical Accumulator                  │   │
                   │  │  • Every quote fetch → OHLCV to PG       │   │
                   │  │  • Builds growing historical corpus      │   │
                   │  │  • Alpaca only gives 5 days of 5-min     │   │
                   │  │    bars — this is how we escape that     │   │
                   │  └─────────────────────────────────────────┘   │
                   └─────────────────────────────────────────────────┘

┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│  Kairos Agent       │    │  Stonks Agent       │    │  Aldridge Agent     │
│  (API key: kairos)  │    │  (API key: stonks)  │    │  (API key: aldridge) │
│                     │    │                     │    │                     │
│  Connects via MCP   │    │  Connects via MCP   │    │  Connects via MCP   │
│  (SSE or stdio)     │    │  (SSE or stdio)     │    │  (SSE or stdio)     │
└─────────────────────┘    └─────────────────────┘    └─────────────────────┘

┌─────────────────────┐
│  Leaderboard UI     │
│  (reads direct from │
│  PG, no API call)   │
└─────────────────────┘
```

## Auth & Security

### Per-Trader API Keys

Each trader agent gets a unique API key stored as an environment variable:

```
TRADING_TERMINAL_KEY_KAIROS=tt_xxxxx
TRADING_TERMINAL_KEY_STONKS=tt_yyyyy
TRADING_TERMINAL_KEY_ALDRIDGE=tt_zzzzz
```

The Terminal resolves the key to a trader identity and scopes:
- `get_portfolio()` → returns only that trader's portfolio
- `submit_order()` → places from that trader's Alpaca account
- `record_journal()` → writes with that trader's ID
- `get_news()`, `get_macro()` → shared, accessible by all

### Alpaca Keys

**Alpaca keys live ONLY in the Terminal's environment** on docker.klo. Agents never see them. The Terminal's `docker-compose.env` or Docker secrets holds:

```
ALPACA_KAIROS_KEY=...
ALPACA_KAIROS_SECRET=...
ALPACA_STONKS_KEY=...
ALPACA_STONKS_SECRET=...
ALPACA_ALDRIDGE_KEY=...
ALPACA_ALDRIDGE_SECRET=...
```

### Threat Model

| Threat | Mitigation |
|--------|-----------|
| Agent key leaks | Key rotation. Keys are bearer tokens, scoped per trader. |
| Agent impersonates another trader | Key scoping. Terminal rejects mismatched trader_id. |
| Alpaca key leak | Alpaca keys never leave docker.klo. Agents can't read them. |
| Unauthenticated requests | Every MCP tool call requires valid key. |

## MCP Tool Definitions

### Data Tools (shared, available to all)

| Tool | Params | Returns | Cache |
|------|--------|---------|-------|
| `get_quotes` | symbols: string[] | OHLCV + RSI per symbol | 5s |
| `get_macro` | — | FOMC, yield curve, GDP, CPI, unemployment | 1h |
| `get_sentiment` | symbol: string | FinBERT + Praesentire scores | 5m |
| `get_sentiment_divergence` | symbol: string | EN vs ZH sentiment, divergence score | 10m |
| `get_flow` | symbol: string | Unusual options flow | 5m |
| `get_insiders` | symbol: string | SEC Form 4 filings | 30m |
| `get_technical_scan` | symbol: string | Multi-TF RSI/MACD/BB (15m/1h/4h/1d) | 5m |
| `get_risk` | symbol: string | VaR, beta, correlation, concentration | 15m |
| `get_market_regime` | — | ML regime signal (bullish/bearish/choppy/sustainable/exhausted) | heartbeats |
| `get_news` | filters: {symbol?, sources?, categories?, since?, limit?} | Archived news entries | instant |
| `get_leaderboard` | — | All trader P&L, positions, rankings | 5s |

### Trading Tools (scoped by API key)

| Tool | Params | Returns | Notes |
|------|--------|---------|-------|
| `get_portfolio` | — | {value, cash, positions[], P&L} | Scoped to API key's trader |
| `get_positions` | — | Open positions with P&L | Scoped to API key's trader |
| `submit_order` | {symbol, qty, side, type, time_in_force} | Order confirmation | Validates against entry gate |
| `cancel_order` | order_id: string | Cancellation confirmation | — |
| `get_orders` | status?, limit? | Order history | Scoped |
| `record_journal` | {entry_type, title, body, metadata?} | Journal record ID | Structured + append-only |
| `record_decision` | {ticker, action, rationale, confidence?} | Decision record ID | For leaderboard/audit |
| `watch_symbol` | symbol: string | Confirmation | Adds to news poller's priority queue |

### Historical Trading Tools

| Tool | Params | Returns | Notes |
|------|--------|---------|-------|
| `get_historical_data` | {symbol, start, end, interval?} | OHLCV bars | From accumulated historical_ticks |
| `run_backtest` | {symbols, start, end, initial_capital?, strategy?} | Backtest results | Uses historical engine |
| `set_mode` | mode: "live" \| "backtest" | Mode confirmed | Switches between live Alpaca and historical engine |

## Database Schema (PostgreSQL)

```sql
-- Traders
CREATE TABLE traders (
    id TEXT PRIMARY KEY,          -- 'kairos', 'stonks', 'aldridge'
    display_name TEXT NOT NULL,
    api_key_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Positions (live + historical)
CREATE TABLE positions (
    id BIGSERIAL PRIMARY KEY,
    trader_id TEXT REFERENCES traders(id),
    symbol TEXT NOT NULL,
    qty NUMERIC NOT NULL,
    avg_entry_price NUMERIC NOT NULL,
    current_price NUMERIC,
    side TEXT NOT NULL DEFAULT 'long',
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    realized_pnl NUMERIC,
    is_live BOOLEAN DEFAULT TRUE,
    backtest_run_id TEXT   -- NULL for live trades
);

-- Orders
CREATE TABLE orders (
    id TEXT PRIMARY KEY,           -- Alpaca order ID or local UUID
    trader_id TEXT REFERENCES traders(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    type TEXT NOT NULL,
    qty NUMERIC NOT NULL,
    status TEXT NOT NULL,
    filled_qty NUMERIC,
    filled_avg_price NUMERIC,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    filled_at TIMESTAMPTZ,
    is_backtest BOOLEAN DEFAULT FALSE
);

-- Journals (trading diaries per agent)
CREATE TABLE journals (
    id BIGSERIAL PRIMARY KEY,
    trader_id TEXT REFERENCES traders(id),
    entry_type TEXT NOT NULL,       -- 'analysis', 'decision', 'reflection', 'error'
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    metadata JSONB,                 -- extra structured data
    ticker TEXT,                    -- optional related symbol
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Decisions (structured trading ticks)
CREATE TABLE decisions (
    id BIGSERIAL PRIMARY KEY,
    trader_id TEXT REFERENCES traders(id),
    ticker TEXT,
    action TEXT NOT NULL,           -- 'buy', 'sell', 'hold', 'cut', 'watch'
    rationale TEXT NOT NULL,
    confidence NUMERIC,             -- 0.0 to 1.0
    regime TEXT,                    -- market regime at decision time
    portfolio_value_at_time NUMERIC,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- News archive (accumulated)
CREATE TABLE news_archive (
    id BIGSERIAL PRIMARY KEY,
    symbols TEXT[],                 -- related tickers
    title TEXT NOT NULL,
    body TEXT,
    source TEXT NOT NULL,           -- 'alpaca', 'alphai', 'reddit', etc.
    url TEXT,
    relevance_score NUMERIC,
    categories TEXT[],
    published_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(url, published_at)       -- dedup
);

-- Historical ticks (OHLCV from Alpaca)
CREATE TABLE historical_ticks (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,          -- '1Min', '5Min', '15Min', '1D'
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

-- Signals (shared signal repository)
CREATE TABLE signals (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,            -- 'kairos', 'stonks', 'aldridge', or external
    ticker TEXT NOT NULL,
    signal_type TEXT NOT NULL,       -- 'momentum', 'value', 'sentiment', 'flow', 'insider'
    direction TEXT NOT NULL,         -- 'bullish', 'bearish', 'neutral'
    strength NUMERIC,                -- 0.0 to 1.0
    rationale TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Watchlist (news poller priority)
CREATE TABLE watchlist (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    requested_by TEXT,              -- trader_id or 'auto'
    priority INTEGER DEFAULT 5,     -- higher = more frequent polling
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_polled_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE
);
```

## Agent Onboarding

### What Changes for Each Trader

Currently each trader has:
- `TOOLS.md` pointing to local data fetching (curl localhost:5000)
- `skill-alpaca-*` for trade execution
- Local append-only journal files

After migration:
- `TOOLS.md` references Trading Terminal MCP tools
- MCP client configured in OpenClaw gateway pointing to docker.klo:PORT
- Each trader keeps **local append-only logs** for their own reasoning
- Structured journals go through `record_journal()` → PG → leaderboard

### Local Append-Only Logs

Agents still maintain local markdown files for their own stream of consciousness:

```
kairos/journal/2026-07-16.md  ← raw thoughts, rejected ideas, notes
```

But structured decisions and trades go through the Terminal. Two fidelity levels:
- **Local**: high-fidelity, stream-of-consciousness, full reasoning chain
- **Terminal**: structured, auditable, leaderboard-visible

### Configuration

In each trader's OpenClaw config:

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

## Historical Trading Engine

### Live vs Backtest Mode

The Terminal has a **mode flag**:

```
mode = "live"     → submit_order() hits Alpaca API
mode = "backtest" → submit_order() hits historical engine
```

### Historical Engine Behavior

In backtest mode:
1. `get_portfolio()` reads from the backtest's simulated account (initial capital + filled orders)
2. `get_quotes(symbol)` returns historical bars for the configured date range
3. `submit_order()` matches against historical OHLCV for fill simulation
4. All trades, journals, and decisions are stored with `is_backtest = TRUE` and a `backtest_run_id`
5. Results appear on the leaderboard alongside live trades (tagged)

### Default Backtest Mode

- **Default**: trade against the **most recent completed trading day**
- Traders can specify: `set_mode("backtest", date="2026-07-15", symbols=["NVDA", "TSLA"], initial_capital=10000)`
- This lets agents "wake up, trade yesterday, see how they did" before markets open

### Data Source for Backtesting

The `historical_ticks` table grows every time `get_quotes()` is called for a live ticker. Over time, it accumulates:

- **Day 1**: 5 days of data (from Alpaca's window)
- **Day N**: 5 + N days of data (from accumulation)
- **Month 3**: ~65 trading days of 5-min bars
- **Year 1**: ~260 trading days

This means backtests get **more accurate over time** as the corpus grows.

## News Aggregation

### Background Poller

```
loop:
  for each source in [Alpaca News, AlphaAI, Reddit, Stocktwits, Bluesky]:
    for each symbol in priority_queue (sorted by priority, watchlist):
      fetch news for symbol from source
      deduplicate by (url, published_at)
      store in news_archive table
      update watchlist.last_polled_at
      sleep(rate_limit_delay)
```

### Rate Limiting

| Source | Rate Limit | Notes |
|--------|-----------|-------|
| Alpaca News | 10 req/min | Reliable, bulk unfiltered |
| AlphaAI | 100 req/day (free) | Use only for high-priority symbols |
| Reddit | Varies | Per-subreddit, rotating accounts |
| Stocktwits | Varies | Public API |
| Bluesky | Varies | AT Protocol, no hard limit |

### Priority Queue

- Default watched symbols: current positions + discovered tickers from all traders
- `watch_symbol("XYZ")` → adds to queue with priority 5
- Trader can set priority: `watch_symbol("XYZ", priority=10)` → polled more frequently
- Symbols with no activity for 7 days → auto-deactivated

### Shared Archive Benefit

If Kairos discovers and watches `NVDA`:
1. News poller adds NVDA to its rotation
2. News fetched → stored in `news_archive`
3. Stonks queries `get_news(symbol="NVDA")` → returns from archive (no new fetch)
4. Aldridge queries `get_news(symbol="NVDA", categories=["macro"])` → filtered from archive

This means **the more traders use the system, the less redundant fetching happens.**

## Deployment

### Current: scp + ssh

```bash
# Build
docker build -t trading-terminal .

# Save
docker save trading-terminal | gzip > terminal.tar.gz

# Deploy
scp terminal.tar.gz docker.klo:/docker/terminal/
ssh docker.klo "cd /docker/terminal && docker compose up -d"
```

```yaml
# docker-compose.yml
version: "3.8"
services:
  terminal:
    build: .
    ports:
      - "5001:5001"
    env_file: .env  # Alpaca keys live here
    depends_on:
      - db
    restart: unless-stopped

  db:
    image: postgres:16
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: trading_terminal
      POSTGRES_USER: terminal
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
    secrets:
      - db_password
    restart: unless-stopped

volumes:
  pgdata:

secrets:
  db_password:
    file: ./secrets/db_password.txt
```

### Future: CI/CD

GitHub Actions → build → scp → ssh docker compose up. Standard pipeline.

## Migration Plan

| Phase | What | When |
|-------|------|------|
| **1** | Deploy Trading Terminal alongside existing system | Before next market open |
| **2** | Switch one trader (Stonks — simplest) to Terminal-only | Day 1 |
| **3** | Verify leaderboard data flows correctly | Day 1-2 |
| **4** | Switch remaining traders | Day 2-3 |
| **5** | Deprecate old data bus / SQLite tables | Day 3-4 |
| **6** | Data migration from old tables | Day 4-5 |

### Rollback

If the Terminal goes down:
- Each trader still has their local tools/knowledge
- Old data bus can be restarted (it's still on .41 VM)
- Fallback: agents can use Alpaca MCP directly as emergency override

## Workboard

Created cards for each major work item:

| Card | Priority | Status |
|------|----------|--------|
| Architecture & Schema Design | Urgent | Triage |
| News Aggregation Engine | High | Triage |
| Historical Data Accumulation Engine | High | Triage |
| Historical Trading Engine | High | Triage |
| Agent Skills & Onboarding | Urgent | Triage |
| Leaderboard V2 + Testing Dashboard | Normal | Triage |
| Deployment & Secrets Pipeline | Urgent | Triage |
| Migration Plan & Cutover Strategy | High | Triage |

---

*This is a living document. Update as design decisions are made.*
