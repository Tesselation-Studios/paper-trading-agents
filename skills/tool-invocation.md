# Skill: Tool Invocation Syntax

Load-bearing tool reference for the tick loop. `executor.py` is the only source of truth for cash/positions/P&L — never the data bus (see `skills/data-bus-fallback.md`).

## Alpaca Executor

```bash
python3 scripts/executor.py --account stonks --action status
python3 scripts/executor.py --account stonks --action BUY --ticker SOFI --qty 2 --price 4.58 --conviction 0.6 --sector "Consumer Tech"
python3 scripts/executor.py --account stonks --action SELL --ticker SOFI --qty 2 --price 4.58
python3 scripts/executor.py --account stonks --action check-stops
```

Keys: `ALPACA_STONKS_KEY` / `ALPACA_STONKS_SECRET`.

**BUY/SELL guardrail gates** (`params.json` → `guardrail_gates`, each independently toggleable; blocked trade exits non-zero with reason):

| Gate | Checks | Needs |
|---|---|---|
| `cash` | cost ≤ available cash | `--price` |
| `position_size` | ticker ≤ `risk.max_position_pct` of portfolio | `--price` |
| `max_positions` | open positions < `risk.max_positions` | — |
| `sector_concentration` | sector ≤ `risk_guards.max_positions_per_sector` | `--sector` |
| `hours` | market open 09:30–16:00 ET Mon–Fri | — |
| `conviction` | ≥ `risk.conviction_floor` | `--conviction` |
| `bankroll` | cost ≤ `bankroll.md` ceiling | `--price` |

Missing field → gate skips (fail-open), never blocks on missing data. Always pass `--price` on SELL — it's what lets the bankroll ceiling adapt.

**Bankroll ceiling**: starts $50, +2%/win, -1%/loss (`scripts/bankroll.py`). Every SELL auto-records win/loss and recalculates — no separate call needed. Check anytime: `python3 bankroll.py`.

**Stop-loss scan** (tick_prompt.md step 5): `--action check-stops` returns positions past hard stop (`risk.stop_loss_pct`) or trailing stop (`risk.trailing_stop_pct`, ratchets up from peak since entry) — anything returned must be sold this tick. Toggle: `guardrail_gates.hard_stop` / `.trailing_stop`.

## Decision Logging

```bash
python3 scripts/record_decision.py decision --ticker SOFI --action BUY --conviction 0.6 \
  --rationale "..." --regime momentum_bull \
  --features '{"sentiment": {"direction": "bullish", "confidence": 0.7}, "technical": {"direction": "bullish", "confidence": 0.6}}'
python3 scripts/record_decision.py close --ticker SOFI --pnl 12.50 --return-pct 4.2
```

BUY/SELL only, not HOLD. Writes to Postgres `trading.decisions` + `trading.training_examples` — signal-level data for "which signal predicted wins."
