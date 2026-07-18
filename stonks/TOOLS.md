## Alpaca Executor (primary — load-bearing, direct account truth, market hours only)
```bash
python3 scripts/executor.py --account stonks --action status
python3 scripts/executor.py --account stonks --action BUY --ticker SOFI --qty 2
python3 scripts/executor.py --account stonks --action SELL --ticker SOFI --qty 2
```
Reads keys from `ALPACA_STONKS_KEY`/`ALPACA_STONKS_SECRET`. Only source of truth for cash/positions/P&L — never the data bus (see `skills/data-bus-fallback.md`).

## Journal
Append to `journal/YYYY-MM-DD.md` during nightly maintenance only. Each entry: strategy version + reflection.

## Workspace Conventions
- `params.json` / `strategy.md` — read every tick
- `strategies/active.md` — working memory; `strategies/watchlist.md` — discovery list
- `positions/*.md` — thesis per position; `off_hours/` — research notes
- `scripts/` — executor, news_collector.py, replay_check.py
- `skills/` — on-demand how-tos (auto-commit, off-hours, data-bus, background)
