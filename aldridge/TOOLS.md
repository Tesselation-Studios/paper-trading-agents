## Data Bus (REST API — localhost:5000)

Get market data:
```bash
curl -s http://localhost:5000/quotes?symbols=SPY,AAPL,KO,WMT,JPM | python3 -m json.tool
curl -s http://localhost:5000/macro | python3 -m json.tool
curl -s http://localhost:5000/market-regime | python3 -m json.tool
```

## Alpaca Executor
```bash
python3 scripts/executor.py --account aldridge --action status
python3 scripts/executor.py --account aldridge --action BUY --ticker KO --qty 1
python3 scripts/executor.py --account aldridge --action SELL --ticker KO --qty 1
```

## Journal
Append to `journal/YYYY-MM-DD.md`. One file per day. Each entry includes strategy version + reflection.

## Workspace Conventions
- `params.json` — numerical params, read every tick
- `strategy.md` — current approach, read every tick
- `strategies/active.md` — detailed playbook
- `positions/*.md` — thesis files for open positions
- `scripts/` — executor, maintenance