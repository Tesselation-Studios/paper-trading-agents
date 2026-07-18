# Heartbeat — Stonks Tick Checklist

## Core Loop (every 5-min tick during market hours)

1. Read params → `read params.json`
2. Read strategy → `read strategy.md`
3. Read playbook → `read strategies/active.md`
4. Read watchlist → `read strategies/watchlist.md`
5. Portfolio check → `python3 scripts/executor.py --account stonks --status` (direct Alpaca — the only source of truth)
6. Market snapshot (best-effort) → data bus sentiment/quotes/regime if healthy; note and skip if stale/down, never block on it
7. Check theses → `read positions/*.md` (only near triggers)
8. Discovery pass → drop stale watchlist names (idle_ticks over threshold), add anything newly noticed
9. Decide BUY/SELL/HOLD — structured JSON, tight rationale, mandate = small-cap + wide diversification
10. Execute via `scripts/executor.py --account stonks` if trade
11. Update thesis → `positions/$TICKER.md` (only if trade or trigger event)
12. `HEARTBEAT_OK`

**Trim rule:** If no trade and no trigger event, keep active.md entry to 3-5 lines. No P&L tables in the journal.

## Three-Step Rhythm (nightly, 16:30 ET)
1. Journal — one concise entry for the day
2. Synthesize — patterns, errors, watchlist turnover, takeaways
3. Evolve — update strategy.md / params.json, commit
4. Git commit (local): `stonks: nightly YYYY-MM-DD — [summary]`

## Strategy Evolution
- Read `strategy.md` every tick. You are NOT limited to it — evolve it when you learn something real.
- Version: `stonks.strat:v{major}.{minor}`. Experimental: `stonks.strat:x-{name}` (5 trades then promote or revert).
- Don't force evolution. "Nothing changed" is a valid nightly.

## Market Regime Reference
- **TRENDING_UP**: Full-size buys (within the small-cap position cap)
- **CHOPPY**: Half size, wider stops, favor names with confirmed catalysts
- **TRENDING_DOWN**: Defensive rotation, reduce exposure, no new buys
