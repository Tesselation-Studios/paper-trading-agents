## Tool Invocation Syntax
Executor + decision-logging command syntax → `skills/tool-invocation.md`.

## Experience Counter
`experience.json` — wins/losses/trades auto-tracked by executor.py. total_trades ≠ win-rate denom (every order); workspace_review.py flags drift vs bankroll_state. Tiers in `bankroll.py`.

## Journal
Append to `journal/YYYY-MM-DD.md` during nightly maintenance only — strategy version + reflection.

## Workspace Conventions
- `params.json` / `strategy.md` — read every tick
- `strategies/active.md` — working memory; discovery/positions in `state/trader.db`
- `off_hours/` — research notes
- `scripts/` — executor + supporting tools, `ls scripts/` for the current list
- `state/` — machine-written local caches, not hand-edited
- `proposals/` — evolution proposals awaiting review, see `skills/evolution-proposals.md`
- `skills/` — tool-invocation, off-hours, data-bus, sentiment-cache, workspace-review, evolution-proposals, prompt-iteration, background, fundamentals, self-improving-agent, freeform-discovery, backtest-tools (each loads by its own trigger)
- **Never run `git commit`/`git add`** — Claude Code owns commits here now. `auto-commit.md` is historical only.
