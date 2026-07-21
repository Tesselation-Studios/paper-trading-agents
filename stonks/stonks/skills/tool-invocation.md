# Skill: Tool Invocation Syntax

## Alpaca Executor (primary — load-bearing, direct account truth, market hours only)
```bash
python3 scripts/executor.py --account stonks --action status
python3 scripts/executor.py --account stonks --action BUY --ticker SOFI --qty 2
python3 scripts/executor.py --account stonks --action SELL --ticker SOFI --qty 2
```
Reads keys from `ALPACA_STONKS_KEY`/`ALPACA_STONKS_SECRET`. Only source of truth for cash/positions/P&L — never the data bus (see `skills/data-bus-fallback.md`).

## Decision Logging (structured, DB — not the markdown journal)
```bash
python3 scripts/record_decision.py decision --ticker SOFI --action BUY --conviction 0.6 \
  --rationale "..." --regime momentum_bull \
  --features '{"sentiment": {"direction": "bullish", "confidence": 0.7}, "technical": {"direction": "bullish", "confidence": 0.6}}'
python3 scripts/record_decision.py close --ticker SOFI --pnl 12.50 --return-pct 4.2
```
Writes to the shared Postgres `trading` schema (`trading.decisions` + `trading.training_examples`) — real signal-level data for "which signal actually predicted wins" analysis. BUY/SELL only, not routine HOLD.
