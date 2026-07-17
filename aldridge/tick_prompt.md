# Tick Prompt — Aldridge Phase 1

**Market tick received.** You are in your persistent trading session. Follow this loop:

## Core Loop

1. **Read active.md** → `read strategies/active.md` — this is your working memory from last tick. Know your last state.

2. **Check portfolio** → `python3 scripts/executor.py --account aldridge --status` — prices moved, stops shifted, P&L changed.

3. **Market snapshot** → fetch current quotes + macro + regime from data bus. Note any regime changes.

4. **Scan positions near triggers** → stop breaches, profit targets, RSI oversold opportunities, concentration limits.

5. **Decide** → BUY/SELL/HOLD with structured JSON. Keep rationale tight.

6. **Execute** → via Alpaca executor if trade. Update thesis file if position changed.

7. **Update active.md** → append your current tick entry. Keep it **trim**:
   - 3-5 lines if no trade and no trigger event
   - Include: regime, curve, portfolio value, top/bottom movers (3 max each), positions near triggers, decision
   - Full P&L tables only when something actually changed

8. **HEARTBEAT_OK**

## Trim Rules

- No re-reading strategy.md or params.json — they're already in your session context.
- No P&L tables in the journal. That goes in active.md only.
- If this tick is identical to last tick (same regime, no triggers), write "Same as last tick" and done.
- Journal entry at EOD only, not per tick.

## Reference

- `strategy.md` — your constitution (beliefs, rules). Already in context.
- `params.json` — numerical params. Already in context.
- `AGENTS.md` — who you are. Already in context.
- `strategies/active.md` — working memory, append each tick.
- `journal/YYYY-MM-DD.md` — concise EOD diary entry only.
- `positions/*.md` — thesis for each open position.
- `scripts/executor.py` — Alpaca order executor.
