## Tool Invocation Syntax
Executor + decision-logging command syntax → `skills/tool-invocation.md`.

## Experience Counter
File: `experience.json`. Read at start of each tick, update after trades:
- Increment `total_ticks` each tick
- Increment `total_trades` / `total_wins` / `total_losses` on closed positions
- Track `consecutive_wins` / `consecutive_losses` streaks
- Universe breadth is no longer a `peak_ceiling` milestone here (2026-07-23: found stuck at $50 since creation, never fired) — it's mechanized in `bankroll.universe_max_price_for_ceiling()`, read live by `scripts/discovery_scan.py` every scan. Nothing to track manually.
- Competition unlock tier is likewise mechanized, not tracked here: `python3 bankroll.py --tier` for real live status/progress.

## Journal
Append to `journal/YYYY-MM-DD.md` during nightly maintenance only. Each entry: strategy version + reflection.

## Workspace Conventions
- `params.json` / `strategy.md` — read every tick
- `strategies/active.md` — working memory; `strategies/watchlist.md` — discovery list
- `positions/*.md` — thesis per position; `off_hours/` — research notes
- `scripts/` — executor, record_decision.py, news_collector.py, replay_check.py, universe_scan.py, workspace_review.py, evolution_proposal.py, llm_replay.py, discovery_scan.py, discovery_urgency_check.py
- `state/` — machine-written local caches (e.g. `sentiment_cache.json`), not hand-edited
- `proposals/` — evolution proposals awaiting review, see `skills/evolution-proposals.md`
- `skills/` — on-demand how-tos (tool-invocation, auto-commit, off-hours, data-bus, sentiment-cache, workspace-review, evolution-proposals, prompt-iteration, background)
