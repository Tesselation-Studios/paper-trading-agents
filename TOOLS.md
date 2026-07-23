## Tool Invocation Syntax
Executor + decision-logging command syntax → `skills/tool-invocation.md`.

## Experience Counter
File: `experience.json`. Read at start of each tick, update after trades:
- Increment `total_ticks` each tick
- Increment `total_trades` / `total_wins` / `total_losses` on closed positions
- Track `consecutive_wins` / `consecutive_losses` streaks
- Update `peak_ceiling` if current ceiling is higher
- Update `current_level` based on milestones:
  - 10 trades → "Apprentice"
  - 50 trades → "Journeyman"
  - 100 trades → "Veteran"
  - Ceiling $100 → expand watchlist to $20 stocks
  - Ceiling $200 → expand to $50 stocks

## Journal
Append to `journal/YYYY-MM-DD.md` during nightly maintenance only. Each entry: strategy version + reflection.

## Workspace Conventions
- `params.json` / `strategy.md` — read every tick
- `strategies/active.md` — working memory; `strategies/watchlist.md` — discovery list
- `positions/*.md` — thesis per position; `off_hours/` — research notes
- `scripts/` — executor, record_decision.py, news_collector.py, replay_check.py, universe_scan.py
- `state/` — machine-written local caches (e.g. `sentiment_cache.json`), not hand-edited
- `skills/` — on-demand how-tos (tool-invocation, auto-commit, off-hours, data-bus, sentiment-cache, background)
