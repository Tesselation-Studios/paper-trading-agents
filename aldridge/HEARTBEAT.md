# Heartbeat — Aldridge Tick Checklist

## Core Loop (every 5-min tick during market hours)

1. Read params → `read params.json`
2. Read strategy → `read strategy.md`
3. Read playbook → `read strategies/active.md`
4. Check theses → `read positions/*.md`
5. Portfolio check → `python3 scripts/executor.py --account aldridge --status`
6. Get market data → quotes + macro + regime from data bus
7. Decide BUY/SELL/HOLD with structured JSON
8. Execute via Alpaca executor
9. Update thesis → write/update `positions/$TICKER.md`
10. Journal → append to `journal/YYYY-MM-DD.md` with git commit tag
11. One-line reflection → "What did I learn this tick?"
12. `HEARTBEAT_OK`

## After Hours (nightly maintenance — 16:30 ET)

1. Review today's journal — what happened?
2. Score signal accuracy — was my reasoning sound?
3. Reflect — is my strategy working? What should change?
4. Update strategy.md if needed — version bump if major change
5. Update params.json if needed — numerical param adjustments
6. Git commit all changes with rationale
7. Trim stale positions files, prune old journal entries
8. Write nightly reflection entry

## Strategy Evolution

- You have a general strategy in `strategy.md`. Read it every tick.
- You are NOT limited to this strategy. If you genuinely believe a different approach would work better, change it.
- When you change your strategy, also update params.json and any other files that need to change.
- Version your strategy with `ald.strat:v{major}.{minor}`.
- Experimental strategies use `ald.strat:x-{name}` — try it for 5 trades, then promote or revert.

## Market Regime Reference
- **TRENDING_UP**: Full size buys, ride momentum
- **CHOPPY**: Half size, wider stops, favor defensives
- **TRENDING_DOWN**: Defensive rotation, reduce exposure, no new buys