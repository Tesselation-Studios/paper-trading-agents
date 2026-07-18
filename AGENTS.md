# Paper Trading Agents — Repo Guide

Version-controlled prompts, strategies, and self-evolution state for the paper-trading system's LLM trader agents. This repo is the **brain** — persona, strategy, params, learnings. It holds no execution code.

## Relation to `paper-trading-rebuild`

Two separate repos, two separate roles:
- **`paper-trading-agents`** (this repo) — trader identity/strategy/config, version-tracked so every trading decision can be traced back to the exact prompt/params state that produced it (`git rev-parse HEAD` at decision time).
- **`paper-trading-rebuild`** — the **engine**: Postgres schema, data bus / signal engine, replay-backtest harness (`src/replay.py`, `src/kairos_backtest.py`), dashboard. Runs as services on `docker.klo`; this repo's traders read/write into its Postgres DB (`trading` schema) but own none of that infrastructure.

Traders run as persistent OpenClaw agent sessions on the homelab gateway. Each trader's live runtime workspace (`~/.openclaw/workspace-trader-<id>/` on the gateway host) is its own local git repo, mirrored here under `<trader>/` for durable, shared history — the gateway workspace is where a trader actually executes each tick; this repo is where that history becomes visible/reviewable outside the gateway.

## Current State (2026-07-18)

- **`stonks/`** — the sole active trader. Consolidated architecture (persistent tick session, nightly journal→synthesize→evolve rhythm, off-hours research routine), strategy v1.2.
- **`aldridge/`**, **`kairos/`** — retired (crons/heartbeats disabled, not deleted). Kept as reference for pulling in trading knowledge — `aldridge/` is the proven architecture Stonks was built on; `kairos/`'s and `aldridge/`'s pruned, non-redundant trading knowledge has already been partially consolidated into `stonks/strategy.md` v1.2. Contents vary in freshness — not everything in every trader directory reflects current thinking; treat older material as reference to mine, not as truth.
- **`design/`** — forward-looking specs, not yet built (e.g. `trading-terminal.md`, a unified MCP server for market data + execution — currently traders use a data-bus/direct-Alpaca split instead).
- **`archive/`** — superseded material kept for history, not for reference (e.g. `legacy-single-agent-prompts/`, from before the per-trader directory split).

## Per-Trader File Conventions

| File | Purpose |
|------|---------|
| `AGENTS.md` | Operational rules only — immutable rules (canonical repo, evolution process, size budget), file map. No persona/identity. |
| `IDENTITY.md` | Identity card — name, role, vibe, emoji |
| `SOUL.md` | Persona/voice |
| `HEARTBEAT.md` | Tick checklist |
| `TOOLS.md` | Tool reference |
| `strategy.md` | Versioned constitution — the only file meant to evolve. Strategy only, no persona. |
| `params.json` | Numerical params backing strategy.md |
| `tick_prompt.md` | Full tick-loop instructions |
| `skills/` | On-demand how-tos (not loaded every tick) |
| `journal/`, `off_hours/` | Diary + off-hours research notes |

**Identity/persona lives only in `IDENTITY.md`/`SOUL.md`** — never restated in `AGENTS.md`, `strategy.md`, `HEARTBEAT.md`, or `TOOLS.md`. Applies to every trader, current and future.

Core always-loaded files (`AGENTS.md`/`HEARTBEAT.md`/`TOOLS.md`/`SOUL.md`) are capped at 1100 characters each — detail beyond that belongs in `skills/`, not in the always-loaded set.
