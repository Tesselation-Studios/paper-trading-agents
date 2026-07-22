# Stonks Trader Workspace — Claude Code Instructions

This repo's real coding standards, conventions, and immutable rules live in **`AGENTS.md`** (same directory) — read it before making any change here. It's the shared source of truth for both the trading agent (Stan) and Claude Code, kept as one file on purpose so the two never drift out of sync.

Quick pointers (see `AGENTS.md` for the actual rules):
- Tests required for new scripts/features — `tests/`, pytest, CI on every push. Work isn't done until tests pass.
- Every commit must push to GitHub immediately — no local-only history.
- Core files (`AGENTS.md`/`HEARTBEAT.md`/`TOOLS.md`/`SOUL.md`) are capped at 1100 chars each.
