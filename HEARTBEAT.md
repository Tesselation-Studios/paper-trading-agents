# Heartbeat — Stonks Tick Checklist

## Core Loop (every 5-min tick, market hours)
1. Read params.json, strategy.md, strategies/active.md, strategies/watchlist.md
2. Portfolio check → `executor.py --account stonks --status` (only source of truth)
3. Market snapshot (best-effort, skip if stale/down) → `skills/data-bus-fallback.md`
4. Check theses → `positions/*.md` (only near triggers)
5. Discovery pass → drop stale watchlist names, add newly noticed
6. Decide BUY/SELL/HOLD — structured JSON, tight rationale, small-cap+wide mandate
7. Execute via executor.py if trade; update thesis file if changed
8. `HEARTBEAT_OK`

**Trim rule:** no trade/trigger → 3-5 line active.md entry, no P&L tables.

## Three-Step Rhythm (nightly, 16:30 ET)
Journal → Synthesize → Evolve → git commit. Rules + regime playbook → `strategy.md`.

Off-hours (daily 20:00 ET, no trading) → `skills/off-hours-research.md`.
