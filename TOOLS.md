## Tool Invocation Syntax
Executor + decision-logging command syntax → `skills/tool-invocation.md`.

## Experience Counter
`experience.json` — read each tick, update ticks/trades/wins/losses/streaks after trades. Universe breadth + competition tier mechanized in `bankroll.py`.

## Journal
Append to `journal/YYYY-MM-DD.md` during nightly maintenance only — strategy version + reflection.

## Workspace Conventions
- `params.json` / `strategy.md` — read every tick
- `strategies/active.md` — working memory; `strategies/watchlist.md` — discovery list
- `positions/*.md` — thesis per position; `off_hours/` — research notes
- `scripts/` — executor + supporting tools, `ls scripts/` for the current list
- `state/` — machine-written local caches, not hand-edited
- `proposals/` — evolution proposals awaiting review, see `skills/evolution-proposals.md`
- `skills/` — tool-invocation, auto-commit, off-hours, data-bus, sentiment-cache, workspace-review, evolution-proposals, prompt-iteration, background, fundamentals, self-improving-agent (each loads by its own trigger)
