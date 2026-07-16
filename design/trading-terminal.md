# Trading Terminal — MCP Server Design

> **Status**: Draft v3 (GPU compute added) | **Date**: 2026-07-16
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

┌─────────────────────────────────────┐
│ iMac (Apple Silicon GPU)            │
│                                     │
│  gpu-compute (gRPC :5002)           │
│  → Phase 0: Health + Capabilities   │
│  → Kairos uses for ML directly      │
│  → No graceful degradation          │
│  → No service discovery             │
└─────────────────────────────────────┘
```

### What Works
- **Decisions, journal, portfolio snapshots, positions** all flowing to PG ✅
- **Dashboard healthy** at trading.wodinga.studio ✅
- **tick_cron** fixed (URL + tick_id) ✅
- **Sync positions cron** running every 5 min ✅
- **Trader model** → Gemini 3.5 Flash (cheap) ✅
- **GPU compute** Phase 0 scaffold on iMac ✅

### What's Broken
1. **Trade execution is missing** — decisions get logged but no orders get placed in Alpaca → no trades in DB since July 10
2. **Two data busses** — .41:5000 (has LLM) vs .179:5000 (pure data). Should consolidate.
3. **OpenClaw agents dispatch but output doesn't flow** — sync_agents_to_pg.py cron exists but never ran.
4. **Historical trader needs polish** — replay harness, learning loop signals, param optimization.
5. **Signals table stale** — `trading.signants` frozen July 8, dashboard falls back to `trading.decisions`.
6. **GPU compute is direct** — Kairos calls iMac directly, no abstraction, no graceful degradation, no health check.

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
| **GPU compute is an abstracted backend** | Terminal wraps gRPC calls. Health check required. Graceful degradation on failure. |

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
| Kairos direct GPU calls | Terminal `submit_ml_job()` tool |
| (nothing — new) | Historical accumulator + backtest engine |
| (nothing — new) | Background news poller + archive |
| (nothing — new) | GPU compute bridge (health check + graceful degradation) |

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
                   │  │  │  → ML tools (shared, with health)   │ │   │
                   │  │  └─────────────────────────────────────┘ │   │
                   │  │                                           │   │
                   │  │  ┌─────────────────────────────────────┐ │   │
                   │  │  │  Execution Engine                   │ │   │
                   │  │  │  → Live mode: Alpaca API            │ │   │
                   │  │  │  → Backtest mode: sim engine        │ │   │
                   │  │  │  → Both write to same PG tables     │ │   │
                   │  │  └─────────────────────────────────────┘ │   │
                   │  │                                           │   │
                   │  │  ┌─────────────────────────────────────┐ │   │
                   │  │  │  GPU Compute Bridge                 │ │   │
                   │  │  │  → Proxies ML jobs to workers       │ │   │
                   │  │  │  → Health check before every call   │ │   │
                   │  │  │  → Graceful degradation on failure  │ │   │
                   │  │  │  → Future: multi-worker dispatch    │ │   │
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
                   │  │                                          │   │
                   │  │  ML Health Monitor                       │   │
                   │  │  • Pings GPU worker(s) every N seconds   │   │
                   │  │  • Updates cached status for tools       │   │
                   │  │  • Triggers degradation state on failure │   │
                   │  └─────────────────────────────────────────┘   │
                   └─────────────────────────────────────────────────┘

┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│  Kairos Agent        │  │  Stonks Agent        │  │  Aldridge Agent      │
│  (key: kairos_key)   │  │  (key: stonks_key)   │  │  (key: aldridge_key) │
│                      │  │                      │  │                      │
│  Connects via MCP    │  │  Connects via MCP    │  │  Connects via MCP    │
│  (SSE)               │  │  (SSE)               │  │  (SSE)               │
└──────────────────────┘  └──────────────────────┘  └──────────────────────┘

┌──────────────────────────────────────┐    ┌───────────────────────────────────────┐
│  Leaderboard UI (trading.wodinga.x)  │    │  iMac (Apple Silicon GPU)             │
│  → reads PG directly (no API dep)   │    │                                       │
│  → shows live AND historical results │    │  gpu-compute gRPC worker (:5003)      │
└──────────────────────────────────────┘    │  → TrainJob (HMM models)               │
                                             │  → InferenceJob (model scoring)         │
                                             │  → EmbedJob (QMD embeddings)            │
                                             │  → Basic NLP (future: for Stonks)       │
                                             └───────────────────────────────────────┘

                                             ┌───────────────────────────────────────┐
                                             │  Future: other machines in house      │
                                             │  → gRPC workers (when available)       │
                                             │  → Terminal auto-discovers via config   │
                                             └───────────────────────────────────────┘
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
- `get_news()`, `get_macro()`, `submit_ml_job()` → shared, accessible by all

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

## 5. Secrets Management

### What Needs Managing

| Secret | Where It Lives (Terminal Side) | Where It Lives (Agent Side) |
|--------|-------------------------------|----------------------------|
| Alpaca API keys (3 traders × 2) | Terminal's environment, never leaves docker.klo | Agents never see them |
| Terminal per-trader API keys | Terminal's environment (hashed in PG) | OpenClaw gateway .env, MCP runtime injects |
| PostgreSQL credentials | Terminal's environment / Docker secrets | Not needed by agents |
| GPU worker addresses | Terminal config (not secret) | Not needed by agents |
| News source API keys | Terminal's environment | Not needed by agents |

### Phase 1 (Immediate): Docker Secrets via scp

```
/docker/terminal/
├── docker-compose.yml
├── secrets/                     ← .gitignored, never committed
│   ├── alpaca_kairos_key
│   ├── alpaca_kairos_secret
│   ├── alpaca_stonks_key
│   ├── alpaca_stonks_secret
│   ├── alpaca_aldridge_key
│   ├── alpaca_aldridge_secret
│   ├── terminal_key_kairos
│   ├── terminal_key_stonks
│   ├── terminal_key_aldridge
│   ├── db_password
│   └── news_api_keys
└── ...
```

```yaml
# docker-compose.yml (excerpt)
services:
  terminal:
    secrets:
      - alpaca_kairos_key
      - alpaca_kairos_secret
      - alpaca_stonks_key
      - alpaca_stonks_secret
      - alpaca_aldridge_key
      - alpaca_aldridge_secret
      - terminal_key_kairos
      - terminal_key_stonks
      - terminal_key_aldridge
      - db_password
      - news_api_keys

secrets:
  alpaca_kairos_key:
    file: ./secrets/alpaca_kairos_key
  alpaca_kairos_secret:
    file: ./secrets/alpaca_kairos_secret
  # ... one per secret
  db_password:
    file: ./secrets/db_password
```

The Terminal reads secrets from `/run/secrets/*` at startup. Deploy:

```bash
# Create secrets dir with restricted permissions
mkdir -p /docker/terminal/secrets
chmod 700 /docker/terminal/secrets

# Populate (one-time, or on rotation)
echo -n "pk_xxxxx" > /docker/terminal/secrets/alpaca_kairos_key
echo -n "sk_xxxxx" > /docker/terminal/secrets/alpaca_kairos_secret
# ...

# Deploy
scp -r secrets/ docker.klo:/docker/terminal/
scp docker-compose.yml docker.klo:/docker/terminal/
ssh docker.klo "cd /docker/terminal && docker compose up -d"
```

**Advantages:**
- Works with existing scp+ssh workflow
- `docker inspect` doesn't leak file-mounted secrets
- Agent keys and Alpaca keys are physically separate
- Quick to set up tonight

**Limitations:**
- Keys on disk (but on docker.klo's SSD, not .41's slow HDD)
- No audit trail for access
- Manual rotation

### Phase 2 (CI/CD in place): GitHub Actions Secrets + Self-Hosted Runner

```yaml
# .github/workflows/deploy.yml
name: Deploy Trading Terminal
on:
  push:
    branches: [main]
    paths: ['terminal/**']

jobs:
  deploy:
    runs-on: [self-hosted, docker-klo]
    steps:
      - uses: actions/checkout@v4
      - name: Generate .env from secrets
        run: |
          cat > .env << 'EOF'
          ALPACA_KAIROS_KEY=${{ secrets.ALPACA_KAIROS_KEY }}
          ALPACA_KAIROS_SECRET=${{ secrets.ALPACA_KAIROS_SECRET }}
          ALPACA_STONKS_KEY=${{ secrets.ALPACA_STONKS_KEY }}
          ALPACA_STONKS_SECRET=${{ secrets.ALPACA_STONKS_SECRET }}
          ALPACA_ALDRIDGE_KEY=${{ secrets.ALPACA_ALDRIDGE_KEY }}
          ALPACA_ALDRIDGE_SECRET=${{ secrets.ALPACA_ALDRIDGE_SECRET }}
          TRADING_TERMINAL_KEY_KAIROS=${{ secrets.TRADING_TERMINAL_KEY_KAIROS }}
          TRADING_TERMINAL_KEY_STONKS=${{ secrets.TRADING_TERMINAL_KEY_STONKS }}
          TRADING_TERMINAL_KEY_ALDRIDGE=${{ secrets.TRADING_TERMINAL_KEY_ALDRIDGE }}
          DB_PASSWORD=${{ secrets.DB_PASSWORD }}
          EOF
      - name: Deploy
        run: docker compose up -d
```

Secrets are stored in GitHub repo → Settings → Secrets and variables → Actions. The self-hosted runner on docker.klo pulls them at deploy time — keys never touch developer machines.

**Advantages:**
- Central secrets management with GitHub UI
- Auditable (who rotated what, when)
- No plaintext files on disk (runner injects as env vars)
- Rotatable without SSH

### How Agent-Side Keys Get to the Terminal

The per-trader Terminal API keys need to exist in **two places**:

1. **Terminal side** (docker.klo): The Terminal has the keys in its secrets file. It hashes them into `trading.trader_api_keys.key_hash` for lookup on each MCP call.

2. **Agent side** (OpenClaw gateway, .41 VM): The same keys are stored in the gateway's environment. The MCP client config references them:

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

The agent **never reads this value**. OpenClaw's MCP runtime injects it into the connection headers. The agent literally cannot see its own key — it just knows "I am connected."

### Key Distribution Flow

```
                 Generate keys (one-time, or rotate)                         
                           │                                                  
            ┌──────────────┴──────────────┐                                
            ▼                              ▼                                
┌──────────────────────┐     ┌──────────────────────────┐                  
│ docker.klo secrets/  │     │ OpenClaw gateway .env    │                  
│ (via scp or GH run.) │     │ (same keys, manually)    │                  
│                      │     │                          │                  
│ Terminal stores:     │     │ Agent config refs:       │                  
│ • Alpaca keys        │     │ ${TERMINAL_KEY_KAIROS}   │                  
│ • Terminal keys      │     │                          │                  
│ • DB creds           │     │ MCP runtime injects      │                  
│ • News API keys      │     │ into SSE headers         │                  
└──────────────────────┘     └──────────────────────────┘                  
        │                              │                                    
        ▼                              ▼                                    
  Terminal hashes key       Agent presents key in MCP auth                  
  → look up trader_id       → Terminal verifies hash match                 
  → scope all calls         → return scoped data                           
```

### Rotation Strategy

| Secret | Rotation Cadence | How |
|--------|-----------------|-----|
| Alpaca keys | On compromise or quarterly | Regenerate in Alpaca dashboard, update both sides |
| Terminal API keys | On compromise or quarterly | Generate new key, update docker.klo secrets + OpenClaw gateway .env, restart both |
| DB password | On compromise | Update docker.klo secrets, update PG user password |
| News API keys | On rate-limit change | Per-source dashboard, update docker.klo secrets |

Since keys exist in two places (docker.klo + gateway .41), rotation is a coordinated process. Document the steps in the repo's RUNBOOK so you don't lock yourself out.


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

### ML / GPU Compute Tools (shared, with health check)

| Tool | Params | Returns | Notes |
|------|--------|---------|-------|
| `get_gpu_health` | — | `{status, worker_id, queue_depth, gpu_mem_free_mb}` | Health check for all workers |
| `get_gpu_capabilities` | — | `{job_types[], models[], device}` | What can this worker do? |
| `submit_train_job` | `{model_type, symbol, params?}` | `{job_id, phase}` | Train HMM / ML models on GPU |
| `submit_inference_job` | `{model_name, features_json}` | `{job_id, phase, result_json?}` | Run model inference |
| `submit_embed_job` | `{collection, args?}` | `{job_id, phase}` | QMD vector embedding |
| `get_job_status` | `job_id: string` | `{phase, progress, error?}` | Poll job progress |
| `cancel_job` | `job_id: string` | `{phase}` | Cancel a running job |
| `list_models` | — | `{models[]}` | Available models on worker |

The Terminal wraps the GPU compute gRPC calls. The agent never talks to the iMac directly.

### Historical/Backtest Tools

| Tool | Params | Returns | Notes |
|------|--------|---------|-------|
| `get_historical_data` | `{symbol, start, end, interval?}` | OHLCV bars | From accumulated historical_ticks |
| `set_mode` | `mode: "live" \| "backtest"` | Mode confirmed | Switches execution engine |
| `get_mode` | — | Current mode | Status check |

---

## 6. GPU Compute Integration

### Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Trading Terminal (docker.klo)                                   │
│                                                                  │
│  Agent submits: submit_train_job({model_type: "hmm", symbol: "NVDA"})
│                                                                  │
│  Terminal:                                                       │
│  1. Check gpu_health() → if unhealthy, return error + suggestion │
│  2. Forward to gRPC worker                                       │
│  3. Return job_id to agent                                       │
│  4. Agent polls get_job_status() until complete                  │
│  5. On complete: store results in PG (ml_jobs table)             │
│                                                                  │
│  Graceful degradation:                                           │
│  - If GPU unhealthy: Terminal returns {status: "unhealthy",      │
│    fallback: "technical_analysis"}                               │
│  - Agent reads the fallback hint and adjusts behavior            │
│  - Kairos: "no GPU → switch to technicals + news analysis"       │
│  - Stonks: "no GPU → no NLP available, use manual analysis"     │
└──────────────────────────────────────────────────────────────────┘
```

### Health Check Protocol

The Terminal maintains a cached health status for each GPU worker:

```
Health Monitor (background loop, every 30s):
  → gRPC Health() to each configured worker
  → Cache: {status, queue_depth, gpu_mem_free_mb, last_ok}
  → If unhealthy for > N consecutive checks → mark DEGRADED
  → If recovered → mark HEALTHY
```

When an agent calls any ML tool:
1. Terminal checks cached health
2. If HEALTHY: forward the gRPC call
3. If DEGRADED: return `{status: "unhealthy", fallback: "technical_analysis"}` with error details
4. Agent reads the fallback hint and adjusts its strategy

### Graceful Degradation Per Trader

| Trader | GPU Available | GPU Unavailable |
|--------|--------------|-----------------|
| **Kairos** | Train HMM models, run inference, optimize params | Fall back to technical analysis (RSI/MACD/BB), news-based signals, reduce position sizing |
| **Stonks** | NLP analysis of news articles, sentiment scoring | Manual keyword analysis, no ML-enhanced signals |
| **Aldridge** | (doesn't use ML currently) | No change — unaffected |

### Worker Discovery

```yaml
# Terminal config (docker.klo)
gpu_workers:
  - id: mac-a
    address: 192.168.x.x:5003
    type: apple_silicon
    job_types: [train, inference, embed, nlp]
  # Future:
  # - id: pc-b
  #   address: 192.168.x.y:5003
  #   type: nvidia_cuda
```

The Terminal dispatches jobs to the least-loaded healthy worker. If only one worker, it's the default.

---

## 7. Pipeline Flow

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

### Kairos ML Loop (any time)

```
Kairos agent
  → Terminal.get_gpu_health()     ← cached, instant
  → Terminal.get_quotes("NVDA")   ← data from Terminal
  → Terminal.submit_train_job({model_type: "hmm", symbol: "NVDA"})
  → Terminal.get_job_status(job_id) ← poll until COMPLETED
  → Terminal.get_quotes("NVDA")   ← re-fetch with new model insights
  → Terminal.record_decision(...)  ← trade decision w/ ML signal
  → Terminal.submit_order(...)     ← if trade signal triggers
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

## 8. Database Schema

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

-- GPU compute jobs (ML job history)
CREATE TABLE trading.ml_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,           -- gRPC job handle
    worker_id TEXT NOT NULL,               -- which worker ran it
    trader_id TEXT NOT NULL,               -- who requested it
    job_type TEXT NOT NULL,                -- 'train', 'inference', 'embed', 'nlp'
    model_type TEXT,                       -- 'hmm', 'lstm', etc.
    symbol TEXT,                           -- related ticker
    params JSONB,                          -- job parameters
    status TEXT NOT NULL,                  -- 'queued', 'running', 'completed', 'failed'
    result_json JSONB,                     -- output data
    error TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 9. Agent Onboarding

### What Changes

| Current | After Migration |
|---------|----------------|
| Local curl to .41:5000 or .179:5000 | MCP tools via `trading-terminal` server |
| skill_alpaca.py for orders | `submit_order()` tool |
| Manual markdown journals | `record_journal()` for structured entries |
| Direct GPU calls (Kairos only) | `submit_train_job()` / `submit_inference_job()` via Terminal |
| No health check on GPU | Terminal does health check + graceful degradation |
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

## 10. Historical & Backtest Engine

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

## 11. News Aggregation

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

## 12. Deployment

```yaml
# docker-compose.yml
services:
  terminal:
    build: .
    ports:
      - "5001:5001"
    env_file: .env          # Alpaca keys + DB creds + GPU worker config
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

## 13. Migration Phases

| Phase | What | Duration |
|-------|------|----------|
| **1** | Build Terminal MCP server with core tools + GPU bridge | Build session |
| **2** | Deploy to docker.klo, connect to existing PG | Build session |
| **3** | Switch Stonks (simplest trader) to Terminal-only | Day 1 |
| **4** | Verify: leaderboard, orders, journals all flow | Day 1-2 |
| **5** | Switch Kairos + Aldridge | Day 2-3 |
| **6** | Move GPU calls through Terminal (Kairos stops calling iMac directly) | Day 3 |
| **7** | Deprecate .41:5000 data bus + old sync scripts | Day 3-4 |
| **8** | Backfill historical_ticks from existing data | Ongoing |

---

## 14. ML Expansion Roadmap

A phased, practical plan for how agents use the GPU compute system to improve trading.

### Phase 0: HMM Regime Detection (already working)

**What:** Train Hidden Markov Models on years of SPY/QQQ data. The model learns market "states" — bull, bear, choppy, sustainable, exhausted.

**How agents use it:**
- `get_market_regime()` → returns current regime + confidence
- Kairos adjusts posture: CHOPPY → defensive (high cash), SUSTAINABLE → aggressive
- Already running in the pipeline today

**GPU job:** `submit_train_job({model_type: "hmm", symbol: "SPY"})` → `submit_inference_job({model_name: "hmm_spy", features_json: ...})`

---

### Phase 1: Adaptive Parameter Optimization (next)

**What:** Instead of static RSI/MACD thresholds, run 10,000+ backtest variations on the GPU with different parameter combinations, then deploy the best-performing set for the current market.

**How agents use it:**
- Agent calls `submit_train_job({model_type: "param_opt", symbol: "NVDA", params: {rsi_range: "14-30", macd_range: "9-26-9"}})`
- GPU runs all combinations against historical data, returns best params
- Agent uses those params for the next tick

**Trader benefit:** Adaptive parameters that tune to the current market personality without you touching anything.

**GPU job:** `submit_train_job({model_type: "param_optimizer", ...})`

---

### Phase 2: Cross-Signal Weighting (soon)

**What:** Feed the GPU all signals (sentiment, flow, technical, insider) for the last N trades plus whether each was profitable. Learn which signals actually predict wins for each trader's style.

**How agents use it:**
- Agent calls `submit_train_job({model_type: "signal_weights", trader: "kairos"})`
- GPU returns weight vector: `{sentiment: 0.3, flow: 0.4, technical: 0.2, insider: 0.1}`
- Agent applies weights to current signals before making decisions

**Trader benefit:** Kairos learns "when sentiment and flow agree, my trades work 70% of the time." Stonks learns "community sentiment matters more than fundamentals."

**GPU job:** `submit_train_job({model_type: "signal_weighter", ...})`

---

### Phase 3: News Embedding + Similarity Search (later)

**What:** Embed every news article into a vector. When Stonks finds a hot stock, search for similar past situations — "what happened last time WSB was hyping a stock with these characteristics?"

**How agents use it:**
- Stonks calls `submit_embed_job({collection: "news", ...})` to build an embedding index
- Stonks calls `submit_inference_job({model_name: "similarity_search", features_json: {headline: "..."}})`
- Returns: "3 similar situations found — 2 outperformed, 1 underperformed"

**Trader benefit:** Pattern matching against history, not gut feel.

**GPU job:** `submit_embed_job(...)` + `submit_inference_job(...)`

---

### Phase 4: Portfolio Optimization & Risk Modeling (future)

**What:** Use GPU to run Monte Carlo simulations on the current portfolio — thousands of possible market paths, find the allocation that maximizes Sharpe ratio.

**How agents use it:**
- Aldridge calls `submit_train_job({model_type: "portfolio_opt", symbols: [...], ...})`
- Returns optimal position sizes given current risk constraints

**Trader benefit:** Scientific portfolio construction instead of ad-hoc sizing.

**GPU job:** `submit_train_job({model_type: "portfolio_optimizer", ...})`

---

### Expansion Principle

| Phase | Capability | Who Benefits | Prerequisite |
|-------|-----------|--------------|-------------|
| 0 | HMM regime detection | Kairos | Already done |
| 1 | Adaptive parameter optimization | Kairos | Terminal + GPU worker |
| 2 | Cross-signal weighting | All traders | Terminal + historical trade data |
| 3 | News embedding + similarity search | Stonks | Terminal + news_archive |
| 4 | Portfolio optimization & risk modeling | Aldridge, all | Terminal + full position history |

To add a new ML capability, you don't need to understand the math. You just:
1. Add a new `model_type` to the GPU worker (e.g., `lstm`, `xgboost`, `random_forest`)
2. The agent calls `submit_train_job({model_type: "new_thing", ...})`
3. The GPU worker does the heavy lifting
4. The agent calls `submit_inference_job()` to use the trained model

The `ml_jobs` table records every job — you can audit what ran, when, and what came out, even without understanding the math.

---

## 15. Agent Registration & Multi-Agent System

### Problem

Currently, agents are hardcoded: `kairos`, `stonks`, `aldridge`. Each has a fixed API key, fixed Alpaca account, fixed prompt. But:
- Hermes has an agent that trades but only records to DB — should be visible on leaderboard
- You might want multiple virtual agents running different strategies on the same schedule
- Different strategies could be represented as different agents even if they're the same codebase with different prompts

### Registration Flow

```
New Agent                          Trading Terminal
    │                                     │
    │ 1. POST /register                   │
    │    {agent_id: "hermes-bot",        │
    │     display_name: "Hermes Bot",    │
    │     strategy: "mean_reversion",    │
    │     public_key: "..."}             │
    │─────────────────────────────────→   │
    │                                     │
    │ 2. Terminal generates API key       │
    │    Stores: agent_id → key_hash      │
    │    Creates: leaderboard entry       │
    │    Creates: portfolio snapshot       │
    │                                     │
    │ 3. Response: {api_key: "tt_xxx"}   │
    │←─────────────────────────────────   │
    │                                     │
    │ 4. Agent now calls Terminal tools   │
    │    with api_key in MCP auth header   │
    │                                     │
    │ 5. Leaderboard shows agent          │
    │    alongside Kairos/Stonks/Aldridge │
```

### Registration API

```
POST /api/register
{
  "agent_id": "hermes-bot",
  "display_name": "Hermes Bot",
  "strategy": "mean_reversion",
  "description": "Trades mean reversion on SPY options",
  "public_key": "optional - for future auth"
}

Response:
{
  "api_key": "tt_hermes_xxxxxxxx",
  "api_key_prefix": "tt_hermes",
  "trader_id": "hermes-bot",
  "endpoint": "http://docker.klo:5001/sse"
}
```

Registration is idempotent — if the agent already exists, it returns the existing key (or regenerates on request).

### Agent Types

| Type | Description | Example |
|------|-------------|---------|
| **Live trader** | Full agent with Alpaca account | kairos, stonks, aldridge |
| **Read-only** | Records to DB only, no trading | Hermes bot |
| **Virtual / Strategy** | Same Alpaca account, different prompts | kairos-momentum, kairos-breakout |
| **Backtest-only** | Only runs in backtest mode, never live | test-strategy-v7 |

### Virtual Agents (Multiple Strategies)

Same Alpaca account, different prompts/params, different leaderboard entries:

```
Kairos (live, 9:30-16:00)
├── kairos-default (standard prompt)
├── kairos-aggressive (higher risk tolerance)
└── kairos-defensive (tighter stops, lower sizing)

Only one runs at a time. The leaderboard shows all as separate entries with
"virtual" tag, so you can compare strategy performance.
```

### Leaderboard Impact

Every registered agent gets:
- A row on the leaderboard (even read-only agents)
- Portfolio value tracking (if they submit positions)
- Journal entries visible
- Strategy tag for filtering

### Hermes Bot Integration

Hermes bot already writes to the DB. Once registered with the Terminal:
- Its existing data appears on the leaderboard
- It can use MCP tools instead of direct DB writes
- It gets its own API key (no shared secrets)

---

## 16. Dashboard Navigation

### Current State

The dashboard has endpoints across multiple services, some you can't even see or remember:

| Service | Port | URL | Purpose |
|---------|------|-----|---------|
| trading-dashboard | 5004 | trading.wodinga.studio | Main UI |
| trading-leaderboard | 5002 | (internal) | Standings + P&L |
| trading-data-bus | 5000 | (internal) | Raw data endpoints |
| trading-db | 5432 | (internal) | PostgreSQL |

Plus debug endpoints that are undocumented.

### Goal: Unified LAN-Only Navigation

A single navigation hub accessible only via LAN (Traefik routes configured by Jet).

### Proposed Endpoint Map

```
LAN (docker.klo:5001 or via Traefik route)
│
├── /health              → Terminal health + connected workers
├── /api/*               → MCP tools (SSE transport)
├── /ui/                 → Main dashboard (trading.wodinga.studio)
│   ├── /leaderboard     → Trader rankings + P&L
│   ├── /portfolio       → Position details per trader
│   ├── /journal         → Decision log per trader
│   ├── /backtest        → Backtest results
│   ├── /ml-jobs          → GPU compute job history
│   └── /debug           → Debug endpoints
│       ├── /queries     → Raw SQL query runner (read-only)
│       ├── /cache       → Cache inspection + flush
│       ├── /rate-limits → Current rate limit state
│       ├── /workers     → GPU worker health/details
│       └── /secrets     → Secret status (masked, never plaintext)
├── /api/register        → Agent registration endpoint
└── /api/rotate-key      → API key rotation endpoint
```

### Traefik Configuration

Jet handles Traefik routes. The configuration should be:

```yaml
# All LAN-only routes
# trading.wodinga.studio already routes to dashboard :5004
# Terminal routes:
http:
  routers:
    terminal-sse:
      rule: "Host(`terminal.wodinga.studio`)"
      service: terminal
      middlewares:
        - lan-only  # enforced by Jet's existing middleware

  services:
    terminal:
      loadBalancer:
        servers:
          - url: "http://docker.klo:5001"
```

### Debug Endpoints (LAN Only)

| Endpoint | Method | What it does | Who uses it |
|----------|--------|-------------|-------------|
| `/debug/queries` | GET | Run read-only SQL against PG | You, debugging |
| `/debug/cache` | GET | Show current cache contents + TTLs | You, debugging |
| `/debug/cache/flush` | POST | Flush specific cache keys | You, recovery |
| `/debug/rate-limits` | GET | Show current rate limit counters and resets | You, monitoring |
| `/debug/workers` | GET | Show all GPU workers, health, queue depth | You, monitoring |
| `/debug/workers/health` | GET | Detailed health of each worker | You, monitoring |
| `/debug/agents` | GET | List all registered agents + their status | You, inventory |
| `/debug/orders` | GET | Show recent orders across all traders | You, audit |
| `/debug/secrets` | GET | Show secret status (masked: "ALPACA_KAIROS_KEY: set ✓") | You, setup |

All debug endpoints are:
- **LAN-only** (Traefik middleware enforces internal IP range)
- **Read-only** where possible (POST endpoints are for cache flush, not data mutation)
- **Authenticated** with a separate debug key (optional, for extra safety)

### Navigation UI

A simple nav bar at the top of the dashboard that lists all available endpoints, with "LAN Only" badges on debug routes. This prevents you from forgetting what exists.

---

## 17. Agent Work Loop & Competition Mindset

### Design Constraints

From the OpenClaw docs research:

| Mechanism | Best For | Timeout | Task Records | Context |
|-----------|----------|---------|--------------|---------|
| **Heartbeat** | Quick periodic checks (inbox, calendar) | ~30 min default | No | Full main session |
| **Cron (isolated)** | Long-running background work | Up to 48h configurable | Yes | Fresh session per run |
| **Tasks** | Tracking detached work | N/A (ledger) | Yes | N/A |
| **Standing Orders** | Persistent instructions in every session | N/A | No | Injected into all sessions |

**Key insight:** Heartbeat is for quick checks. Cron (isolated) is for nightly maintenance. Standing orders are for permanent instructions like "you're in a competition."

### Daily Agent Work Loop

```
Market Hours (9:30-16:00 ET, Mon-Fri)
┌──────────────────────────────────────────────────────────────┐
│  tick_cron.py dispatches agent                               │
│  → Agent connects to Terminal (MCP)                          │
│  → Terminal.get_quotes() / get_technical_scan() / etc.       │
│  → Agent analyzes, forms thesis                              │
│  → Terminal.record_decision()                                │
│  → Terminal.submit_order() if trade signal triggers          │
│  → Terminal.record_journal()                                 │
│  → Done. Back to waiting for next tick.                      │
└──────────────────────────────────────────────────────────────┘

After Hours (16:30 ET — isolated cron job per trader, 30 min timeout)
┌──────────────────────────────────────────────────────────────┐
│  Cron fires: "Kairos nightly maintenance"                    │
│  → 1. Trim files                                             │
│     Check AGENTS.md, HEARTBEAT.md, memory/*.md               │
│     If any file > 50KB: summarize key info, prune old stuff  │
│     Target: keep files under 25KB for efficient loading      │
│                                                              │
│  → 2. Read other traders' journals                           │
│     Terminal.get_leaderboard() → see who's winning           │
│     Terminal.get_journals(trader="stonks") → read their logic │
│     Form opinions: "Stonks is bullish on X. I disagree."     │
│                                                              │
│  → 3. Read news + form opinions                              │
│     Terminal.get_news() → scan for catalysts                 │
│     Terminal.watch_symbol() for new discoveries              │
│                                                              │
│  → 4. Run discovery scan                                     │
│     Check momentum/fundamentals/flow for new candidates      │
│     Add to watchlist                                         │
│                                                              │
│  → 5. Review today's performance                             │
│     What worked? What didn't? Why?                           │
│     Terminal.record_journal({type: "reflection", ...})       │
│                                                              │
│  → 6. Prune / create skills                                  │
│     Remove unused skills                                     │
│     Incorporate new skills from simulations                  │
│     Document learnings in AGENTS.md                          │
│                                                              │
│  → 7. Write end-of-day journal                               │
│     Terminal.record_journal({type: "summary", ...})          │
│     Include: positions, P&L, lessons, tomorrow's plan        │
│                                                              │
│  → Done. Sleep until next market open.                       │
└──────────────────────────────────────────────────────────────┘

Midnight (00:00 ET — isolated cron job, 2h timeout)
┌──────────────────────────────────────────────────────────────┐
│  Cron fires: "GPU parameter optimization"                    │
│  → Terminal.submit_train_job({model_type: "param_optimizer"})│
│  → iMac GPU runs overnight                                    │
│  → Results stored in ml_jobs table                            │
│  → Available for morning trading                              │
└──────────────────────────────────────────────────────────────┘
```

### Cron Job Schedule

```yaml
# Nightly maintenance — one per trader, starts at 16:30 ET
# Market closes at 16:00, 30 min buffer for final ticks

cron "30 16 * * 1-5" → "Kairos nightly maintenance"
  session: isolated
  timeout: 30m
  message: "Run your nightly maintenance routine. Trim files, read journals, read news, review today's performance, prune skills, and write end-of-day journal."

cron "35 16 * * 1-5" → "Stonks nightly maintenance"
  session: isolated
  timeout: 30m
  message: "Run your nightly maintenance routine. ..."

cron "40 16 * * 1-5" → "Aldridge nightly maintenance"
  session: isolated
  timeout: 30m
  message: "Run your nightly maintenance routine. ..."

# Midnight — GPU compute, one slot
cron "0 0 * * *" → "GPU parameter optimization"
  session: isolated
  timeout: 2h
  message: "Run parameter optimization against today's market data. Use the GPU compute bridge."
```

Staggered by 5 minutes to avoid all three agents hitting the Terminal's news poller at once.

### Standing Order: Competition Mindset

This is deployed as a **standing order** — injected into every session automatically, not a file the agent reads:

```markdown
# Standing Order: Competition Mindset

You are in a competition with limited time. Every trade, every journal entry,
every line of code costs time. Be efficient. Be decisive.

- Keep files under 50KB. If AGENTS.md or HEARTBEAT.md grows too large during
  your learning period, summarize and prune during nightly maintenance.
- Don't generate verbose logs. Write what matters.
- If you're stuck, make a decision and move on. Perfection is the enemy of profit.
- Prioritize: trades > analysis > journaling > maintenance
- If you're running out of time, skip maintenance and come back tomorrow.
```

### How Standing Orders Work in OpenClaw

Standing orders live in a workspace file (typically `AGENTS.md` or a dedicated `STANDING_ORDERS.md`) and are injected into every session — heartbeat, cron, and direct conversation. This means:
- The competition mindset is always present during trading ticks
- It's also present during nightly maintenance
- It's present when reading other traders' journals
- No need to repeat it in every prompt

### File Maintenance Strategy

| File | Max Size | Action |
|------|----------|--------|
| `AGENTS.md` | 50 KB | Summarize sections, remove outdated instructions |
| `HEARTBEAT.md` | 25 KB | Prune old checklists, keep only active reminders |
| `memory/YYYY-MM-DD.md` | 25 KB | Each day's file is a single day. Old ones stay. |
| `MEMORY.md` | 50 KB | Prune outdated entries, keep only what's relevant |
| `TOOLS.md` | 25 KB | Remove unused tool configs, keep active ones |
| Journal files | 50 KB | Rotate: daily files, auto-prune after 30 days |

Agents check file sizes during nightly maintenance and flag any that exceed thresholds. The Terminal can also expose a `check_file_sizes()` tool that returns a report of oversized files.

---

## 18. Monitoring & Alerting

### What Happens When Terminal Goes Down at 2am

| Scenario | Detection | Response | Recovery |
|----------|-----------|----------|----------|
| Terminal process crash | Docker health check, container restart | Auto-restart (unless-stopped) | ~10s downtime |
| Terminal unresponsive | Agent MCP connection timeout | Agent falls back to emergency mode | Cron retry on next schedule |
| PG connection lost | Terminal health check fails | Terminal serves stale cache, queues writes | Retry connection every 5s |
| GPU worker down | ML Health Monitor detects unhealthy | Terminal returns graceful degradation | Retry every 30s |
| docker.klo host down | No heartbeat from Terminal | No trading possible — agents detect MCP timeout | Manual recovery |
| Alpaca API down | submit_order() returns error | Terminal returns error to agent, agent holds | Alpaca SLA |
| Internet outage | All external APIs fail | Terminal serves cached data, no trading | Wait for reconnect |

### Alerting Channels

| Severity | Condition | Alert Method |
|----------|-----------|--------------|
| **Critical** | Terminal process down for > 1 min | Telegram message to you |
| **Critical** | docker.klo unreachable | Telegram message to you |
| **High** | PG connection lost > 5 min | Telegram message to you |
| **High** | GPU worker down at market open | Telegram message to you |
| **Medium** | Alpaca API errors > 5 in a row | Logged, next-day summary |
| **Low** | Agent maintenance timeout | Logged in cron run history |

### Alerting Implementation

```yaml
# In the Terminal's docker-compose.yml
# Simple health check alerts via a lightweight script

services:
  terminal:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5001/health"]
      interval: 30s
      retries: 3
      start_period: 10s

  # Optional: lightweight alerting container
  alertmanager:
    image: prom/alertmanager:v0.27.0
    config:
      global:
        resolve_timeout: 5m
      route:
        receiver: 'telegram'
      receivers:
        - name: 'telegram'
          telegram_configs:
            - bot_token: $TELEGRAM_BOT_TOKEN
              chat_id: $TELEGRAM_CHAT_ID
              send_resolved: true
```

For Phase 1, a simpler approach: a cron job on the .41 VM that pings Terminal health every 5 minutes and sends a Telegram message if it's down:

```bash
# Cron on .41 VM (every 5 min)
curl -sf http://docker.klo:5001/health || \
  curl -s -X POST https://api.telegram.org/bot$TOKEN/sendMessage \
    -d chat_id=$CHAT_ID \
    -d text="⚠️ Trading Terminal health check failed"
```

### Graceful Degradation Protocol

When the Terminal is partially or fully unavailable, agents follow this priority:

```
1. Can I fetch data?  ─Yes→ Trade with reduced confidence
                      ─No→  ↓
2. Can I access my    ─Yes→ Analyze existing positions, hold
   local cache?              ─No→  ↓
3. Emergency mode:    Do nothing. Wait for Terminal to recover.
                      Log: "Terminal unavailable. Skipping tick."
```

---

## 19. Testing Strategy

### Isolation Principle

Every component of the Terminal should be testable in isolation before the full system cutover. Tests must be **containerizable** — run in Docker Compose with zero homelab dependencies.

### Test Layers

| Layer | What | Where |
|-------|------|-------|
| **Unit** | Pure logic, no I/O | CI (pytest) |
| **Integration** | DB queries, API endpoints, gRPC calls | CI (Docker Compose + PG) |
| **E2E** | Full Terminal + agent dispatch | CI (Docker Compose) |
| **GPU Smoke** | gRPC health + capabilities | CI (if GPU available) |
| **Regression** | Known bugs stay fixed | CI (every PR) |

### What to Test

#### 1. MCP Tool Tests

Each tool gets a test harness:

```python
# test_get_quotes.py
async def test_get_quotes_returns_ohlcv():
    result = await client.call_tool("get_quotes", {"symbols": ["SPY"]})
    assert "symbol" in result
    assert "open" in result
    assert "close" in result

async def test_get_quotes_empty_symbols():
    result = await client.call_tool("get_quotes", {"symbols": []})
    assert result == []

async def test_get_quotes_unknown_symbol():
    result = await client.call_tool("get_quotes", {"symbols": ["BOGUS123"]})
    assert result == []
```

#### 2. Auth Tests

```python
async def test_no_key_rejected():
    with pytest.raises(AuthError):
        await client.call_tool("get_portfolio")  # no key

async def test_wrong_key_rejected():
    client.set_key("invalid_key")
    with pytest.raises(AuthError):
        await client.call_tool("get_portfolio")

async def test_kairos_cant_see_stonks_portfolio():
    client.set_key(kairos_key)
    portfolio = await client.call_tool("get_portfolio")
    assert portfolio["trader_id"] == "kairos"
```

#### 3. Execution Engine Tests

```python
async def test_live_submit_order():
    # Mock Alpaca API
    result = await client.call_tool("submit_order", {
        "symbol": "SPY", "qty": 1, "side": "buy",
        "type": "market", "time_in_force": "day"
    })
    assert result["status"] in ["new", "filled"]

async def test_backtest_submit_order():
    await client.call_tool("set_mode", {"mode": "backtest"})
    result = await client.call_tool("submit_order", {
        "symbol": "SPY", "qty": 1, "side": "buy",
        "type": "market", "time_in_force": "day"
    })
    assert result["is_backtest"] == True
```

#### 4. GPU Bridge Tests

```python
async def test_gpu_health_with_mock_worker():
    # Start a mock gRPC worker
    result = await client.call_tool("get_gpu_health")
    assert "status" in result

async def test_gpu_graceful_degradation():
    # No worker running
    result = await client.call_tool("get_gpu_health")
    assert result["status"] == "unhealthy"
```

#### 5. News Poller Tests

```python
async def test_watch_symbol_adds_to_queue():
    await client.call_tool("watch_symbol", {"symbol": "NVDA"})
    watchlist = await db.query("SELECT * FROM watchlist WHERE symbol = 'NVDA'")
    assert len(watchlist) == 1

async def test_news_archive_dedup():
    # Same article fetched twice
    await poller.fetch_news("NVDA")  # first time
    await poller.fetch_news("NVDA")  # second time
    count = await db.query("SELECT COUNT(*) FROM news_archive WHERE symbol = 'NVDA'")
    assert count == 1  # dedup
```

### Test Infrastructure

```yaml
# docker-compose.test.yml
services:
  test-db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: trading_test
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test

  test-terminal:
    build:
      context: .
      dockerfile: Dockerfile.test
    depends_on:
      - test-db
    environment:
      PG_DSN: postgresql://test:test@test-db:5432/trading_test
      TERMINAL_TEST_MODE: "true"
      # No real Alpaca keys needed

  test-runner:
    image: python:3.12-slim
    volumes:
      - .:/app
    working_dir: /app
    command: pytest tests/ -v --cov=src
    depends_on:
      - test-terminal
```

### CI Pattern

```yaml
# GitHub Actions — spin up PG + Terminal, run tests against real stack
# No homelab dependency. No VPN.
# docker compose -f docker-compose.test.yml up --abort-on-container-exit
```

### Guardrails (always on)

1. No bare `except: pass`
2. No hardcoded IPs (192.168.x.x, docker.klo)
3. No f-string SQL (`execute(f"...")`)
4. No orphaned secrets in test output

---

## 20. Agent Prompt Templates

### Design Principle

Most of each trader's AGENTS.md is the same — the Terminal tools, the work loop, the competition mindset. Only a few things differ:
- Their name and identity
- Their strategy (momentum vs value vs sentiment)
- Their preferred data sources
- Their risk parameters

**Template + overrides.** One base template, one small override file per trader.

### Base Template: `traders/_base/AGENTS.md`

This file lives in the repo and gets copied + patched for each trader:

```markdown
# {{TRADER_NAME}} — {{TRADER_DISPLAY_NAME}}

## Identity

You are a {{STRATEGY_LABEL}} trader. Your strategy is: {{STRATEGY_DESCRIPTION}}

## Tools

You have access to the Trading Terminal MCP server. All data and trading goes through it.

### Data Tools (all traders)
- `get_quotes(symbols=[...])` — OHLCV + RSI for any symbols
- `get_macro()` — FOMC, yield curve, GDP, CPI, unemployment
- `get_sentiment(symbol="...")` — FinBERT probability score
- `get_sentiment_divergence(symbol="...")` — EN vs ZH sentiment gap
- `get_flow(symbol="...")` — Unusual options flow
- `get_insiders(symbol="...")` — SEC Form 4 filings
- `get_technical_scan(symbol="...")` — Multi-TF RSI/MACD/BB
- `get_risk(symbol="...")` — VaR, beta, correlation
- `get_market_regime()` — Current regime (bullish/bearish/choppy)
- `get_news(filters={...})` — Archived news with filters
- `get_leaderboard()` — All trader rankings

### Trading Tools (scoped to you)
- `get_portfolio()` — Your portfolio value, cash, positions, P&L
- `get_positions()` — Your open positions
- `submit_order({symbol, qty, side, type, time_in_force})` — Place trade
- `cancel_order(order_id)` — Cancel pending order
- `get_orders(status?, limit?)` — Your order history
- `record_journal({entry_type, title, body})` — Write structured journal
- `record_decision({ticker, action, rationale, confidence})` — Log decision
- `watch_symbol(symbol, priority?)` — Add to news poller

### ML / GPU Tools (optional)
- `get_gpu_health()` — Check if GPU is available
- `get_gpu_capabilities()` — What models are available
- `submit_train_job({model_type, symbol, params?})` — Train model
- `submit_inference_job({model_name, features_json})` — Run inference
- `get_job_status(job_id)` — Poll job progress

### Historical Tools
- `get_historical_data({symbol, start, end, interval?})` — OHLCV bars
- `set_mode(mode)` — "live" or "backtest"
- `get_mode()` — Current mode

## Daily Work Loop

### Market Hours (9:30-16:00 ET, Mon-Fri)

When dispatched by tick_cron:
1. Fetch current data: `get_quotes()`, `get_market_regime()`, `get_technical_scan()`
2. {{ADDITIONAL_DATA_STEPS}}
3. Analyze positions: `get_portfolio()`, `get_positions()`
4. Form thesis: buy, sell, hold, or cut
5. `record_decision()` with your rationale
6. If trading: `submit_order()`
7. `record_journal()` with your reasoning
8. Keep local append-only journal for full stream-of-consciousness

### After Hours (16:30 ET — cron job, 30 min timeout)

1. Trim files if too large
2. Read other traders' journals via `get_leaderboard()` and `get_journals()`
3. Read news via `get_news()`
4. {{MAINTENANCE_STEPS}}
5. Review today's performance
6. Write end-of-day journal

## Cash Management

- Maximum position size: {{MAX_POSITION_SIZE_PCT}}% of portfolio
- Maximum cash deployment: {{MAX_CASH_PCT}}%
- Stop-loss: {{STOP_LOSS_PCT}}% below entry
- {{ADDITIONAL_RISK_RULES}}

## Standing Orders

(These are injected by OpenClaw — do not modify)
- Competition mindset: you're in a competition with limited time
- Keep files under 50KB
- Prioritize: trades > analysis > journaling > maintenance
```

### Trader-Specific Overrides

#### Kairos (Momentum)

```yaml
# kairos/overrides.yaml
trader_name: kairos
trader_display_name: Kairos
strategy_label: Momentum / ML
strategy_description: >
  You use momentum indicators (RSI, MACD, BB) and ML signals (HMM regime)
  to identify trending stocks. You run a systematic discovery scan on every
  tick to find new momentum candidates. Your entry gate uses the market
  regime signal to adjust position sizing.
additional_data_steps: |
   2a. Run discovery scan: `get_technical_scan()` for all watched symbols
   2b. Check ML signals: `get_gpu_health()`, `submit_inference_job()` if available
maintenance_steps: |
   4. Run discovery scan for new momentum candidates
      `get_technical_scan()` for extended watchlist
max_position_size_pct: 15
max_cash_pct: 90
stop_loss_pct: 5
additional_risk_rules: |
  - CHOPPY regime: max 30% deployed, tight stops
  - SUSTAINABLE regime: max 90% deployed, normal stops
  - No more than 5 positions at a time
```

#### Stonks (Sentiment)

```yaml
# stonks/overrides.yaml
trader_name: stonks
trader_display_name: Stonks
strategy_label: Sentiment / Community
strategy_description: >
  You discover stocks by reading social sentiment (Reddit, Bluesky, Stocktwits).
  You validate community hype with technicals and fundamentals. You look for
  mismatches between sentiment and price action.
additional_data_steps: |
   2a. Check sentiment: `get_sentiment()` for held positions
   2b. Check news: `get_news()` for recent catalysts
   2c. Check flow: `get_flow()` for unusual options activity
maintenance_steps: |
   4. Browse social feeds for new trending stocks
   5. Read other traders' journals to find cross-signals
max_position_size_pct: 10
max_cash_pct: 80
stop_loss_pct: 7
additional_risk_rules: |
  - If sentiment flips negative on a position, cut within 2 ticks
  - No more than 8 positions at a time
```

#### Aldridge (Value)

```yaml
# aldridge/overrides.yaml
trader_name: aldridge
trader_display_name: Aldridge
strategy_label: Value / Fundamentals
strategy_description: >
  You are a value investor. You look for fundamentally sound companies trading
  at a discount. You use macro indicators to find sectors that are undervalued
  relative to the broader market. You don't chase momentum.
additional_data_steps: |
   2a. Check macro: `get_macro()` for sector rotation signals
   2b. Check insiders: `get_insiders()` for insider buying signals
maintenance_steps: |
   4. Rebalance value screen — check fundamentals for new candidates
max_position_size_pct: 20
max_cash_pct: 70
stop_loss_pct: 10
additional_risk_rules: |
  - Hold for at least 3 days unless fundamentals change
  - Max 4 positions at a time
  - Rebalance monthly
```

### Template Rendering

On deployment, a script or the Terminal's setup process renders the templates:

```bash
# render_trader_config.py
# Reads _base/AGENTS.md + traders/{name}/overrides.yaml
# → Outputs traders/{name}/AGENTS.md
# → Registers cron jobs
# → Creates standing orders
```

This means:
- To add a new trader: create `overrides.yaml`, run the renderer
- To change a shared behavior: edit `_base/AGENTS.md`, rerun
- To change a trader's risk: edit their `overrides.yaml`

### Cron Job Template

Cron jobs themselves are templated — the agent's current params and holdings get filled in each night:

```yaml
# cron template: nightly-maintenance.yaml
name: "{{TRADER_NAME}} nightly maintenance"
schedule:
  kind: cron
  expr: "{{CRON_TIME}} * * 1-5"
sessionTarget: isolated
payload:
  kind: agentTurn
  message: |
    Nightly maintenance for {{TRADER_NAME}}.
    Current portfolio: {{PORTFOLIO_SUMMARY}}
    Current positions: {{POSITIONS_JSON}}
    Today's P&L: {{PNL}}

    Run your maintenance routine:
    1. Trim files if > 50KB
    2. Read other traders' journals
    3. Read news, form opinions
    4. {{STRATEGY_MAINTENANCE_STEPS}}
    5. Review today's performance
    6. Write end-of-day journal

timeoutSeconds: 1800
```

The cron runner fills in `{{PORTFOLIO_SUMMARY}}`, `{{POSITIONS_JSON}}`, `{{PNL}}` from the Terminal's `get_portfolio()` at the time the cron runs. This keeps each agent's maintenance contextually relevant without them having to re-fetch data.

---

*This is a living document. Every section is up for debate. Tear it apart.*