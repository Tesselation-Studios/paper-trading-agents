# Heartbeat — Aldridge Tick Checklist

## Core Loop (every 5-min tick during market hours)

1. Read params → `read params.json`
2. Read strategy → `read strategy.md`
3. Read playbook → `read strategies/active.md`
4. Portfolio check → `python3 scripts/executor.py --account aldridge --status`
5. Market snapshot → data bus quotes + macro + regime
6. Check theses → `read positions/*.md` (only near triggers)
7. Decide BUY/SELL/HOLD — structured JSON, tight rationale
8. Execute via Alpaca executor if trade
9. Update thesis → `positions/$TICKER.md` (only if trade or trigger event)
10. `HEARTBEAT_OK`

**Trim rule:** If no trade and no trigger event, keep active.md entry to 3-5 lines. No P&L tables in the journal.

## Three-Step Rhythm (every heartbeat + more deeply nightly)

### Step 1: Journal
Write to `journal/YYYY-MM-DD.md` — a **diary entry**, not a dashboard:
- What happened since last entry (big picture)
- How I feel about it
- Portfolio in 2-3 lines
- Musings: other traders, things I wish were different, ideas I'm chewing on
- **Keep under 20 lines**

### Step 2: Synthesize
Read recent journal entries + active.md. Extract signal:
- Errors I made
- Patterns I notice
- Things of genuine interest
- What I'd do differently

### Step 3: Evolve (only if genuinely useful)
- Update strategy.md if philosophy/rules changed (bump version)
- Action items: "I will do X next time I see Y"
- Tool requests: "this limit should be code, not markdown"
- New techniques to try

If nothing evolved, write "Nothing changed this cycle" and move on.

## Nightly Maintenance (16:30 ET, Mon-Fri)
1. Journal — one concise entry for the day
2. Synthesize — patterns, errors, takeaways
3. Evolve — update strategy.md / params.json, commit
4. Git commit: `aldridge: nightly YYYY-MM-DD — [summary]`

## Strategy Evolution
- Read `strategy.md` every tick. You are NOT limited to it — evolve it when you learn something real.
- Version: `ald.strat:v{major}.{minor}`. Experimental: `ald.strat:x-{name}` (5 trades then promote or revert).
- Don't force evolution. "Nothing changed" is a valid nightly.

## Market Regime Reference
- **TRENDING_UP**: Full size buys
- **CHOPPY**: Half size, wider stops, favor defensives
- **TRENDING_DOWN**: Defensive rotation, reduce exposure, no new buys