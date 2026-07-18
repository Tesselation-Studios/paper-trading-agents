## Alpaca Executor (primary — load-bearing, direct account truth)
```bash
python3 scripts/executor.py --account stonks --action status
python3 scripts/executor.py --account stonks --action BUY --ticker SOFI --qty 2
python3 scripts/executor.py --account stonks --action SELL --ticker SOFI --qty 2
```
Reads Alpaca API keys from `ALPACA_STONKS_KEY` / `ALPACA_STONKS_SECRET` env vars. This is the only source of truth for cash/positions/P&L — never the data bus.

## Data Bus (REST API — localhost:5000) — best-effort context only, never load-bearing
```bash
curl -s http://localhost:5000/quotes?symbols=SPY,AAPL,SOFI,PLTR | python3 -m json.tool
curl -s http://localhost:5000/sentiment?symbols=SOFI,NVDA | python3 -m json.tool
curl -s http://localhost:5000/momentum | python3 -m json.tool
curl -s http://localhost:5000/fear_greed | python3 -m json.tool
curl -s http://localhost:5000/macro | python3 -m json.tool
curl -s http://localhost:5000/risk | python3 -m json.tool
```
If any of these are down or return stale timestamps, note it in active.md and proceed without them. This service has been the recurring cause of stale-data outages across prior builds — do not let a tick stall waiting on it.

## Journal
Append to `journal/YYYY-MM-DD.md`. One file per day, written during nightly maintenance only. Each entry includes strategy version + reflection.

## Workspace Conventions
- `params.json` — numerical params, read every tick
- `strategy.md` — current constitution, read every tick
- `strategies/active.md` — trimmed working memory, appended each tick
- `strategies/watchlist.md` — growing/shrinking discovery list (the MVP's discovery mechanism)
- `positions/*.md` — thesis files for open positions
- `scripts/` — executor, maintenance
- `skills/auto-commit.md` — commit workspace changes locally after edits
