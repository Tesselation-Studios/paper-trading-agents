## Tool Invocation Syntax
Executor + decision-logging command syntax → `skills/tool-invocation.md`.

## Experience Counter
`experience.json` — trades/wins/losses auto-tracked by executor.py (7/27 fix, prior hand-edit stalled); total_ticks still manual. Tiers in `bankroll.py`.

## Journal
Append to `journal/YYYY-MM-DD.md` during nightly maintenance only — strategy version + reflection.

## Workspace Conventions
- `params.json` / `strategy.md` — read every tick
- `strategies/active.md` — working memory; discovery/positions in `state/trader.db`
- `off_hours/` — research notes
- `scripts/` — executor + supporting tools, `ls scripts/` for the current list
- `state/` — machine-written local caches, not hand-edited
- `proposals/` — evolution proposals awaiting review, see `skills/evolution-proposals.md`
- `skills/` — tool-invocation, auto-commit, off-hours, data-bus, sentiment-cache, workspace-review, evolution-proposals, prompt-iteration, background, fundamentals, self-improving-agent, freeform-discovery, backtest-tools (each loads by its own trigger)
