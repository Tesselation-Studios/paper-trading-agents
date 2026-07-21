# Skill: Tool Invocation Syntax

## Alpaca Executor (primary — load-bearing, direct account truth, market hours only)
```bash
python3 scripts/executor.py --account stonks --action status
python3 scripts/executor.py --account stonks --action BUY --ticker SOFI --qty 2 --price 4.58 --conviction 0.6 --sector "Consumer Tech"
python3 scripts/executor.py --account stonks --action SELL --ticker SOFI --qty 2 --price 4.58
```
Reads keys from `ALPACA_STONKS_KEY`/`ALPACA_STONKS_SECRET`. Only source of truth for cash/positions/P&L — never the data bus (see `skills/data-bus-fallback.md`). BUY/SELL runs through a mechanical guardrail check first (position size, max positions, sector concentration, market hours, conviction floor — gate functions live at the top of `scripts/executor.py`); a blocked trade exits non-zero with the gate reason instead of placing the order. `--price`/`--conviction`/`--sector` feed those checks — pass your best estimate, omitting them just skips the gates that need that field (fail-open, never blocks on missing data). Any single gate can be turned off in `params.json`'s `guardrail_gates` block without a code change.

## Guardrail Stop-Loss Scan (step 5 of tick_prompt.md)
```bash
python3 scripts/executor.py --account stonks --action check-stops
```
Returns any positions past their hard stop (`risk.stop_loss_pct`) or trailing stop (`risk.trailing_stop_pct`, ratchets up from peak price since entry). Anything returned must be sold this tick. Either check can be disabled via `params.json`'s `guardrail_gates.hard_stop` / `.trailing_stop`.

## Decision Logging (structured, DB — not the markdown journal)
```bash
python3 scripts/record_decision.py decision --ticker SOFI --action BUY --conviction 0.6 \
  --rationale "..." --regime momentum_bull \
  --features '{"sentiment": {"direction": "bullish", "confidence": 0.7}, "technical": {"direction": "bullish", "confidence": 0.6}}'
python3 scripts/record_decision.py close --ticker SOFI --pnl 12.50 --return-pct 4.2
```
Writes to the shared Postgres `trading` schema (`trading.decisions` + `trading.training_examples`) — real signal-level data for "which signal actually predicted wins" analysis. BUY/SELL only, not routine HOLD.
