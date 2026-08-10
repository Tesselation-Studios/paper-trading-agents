# Skill: Tool Invocation Syntax

Load-bearing tool reference for the tick loop. `executor.py` is the only source of truth for cash/positions/P&L — never the data bus (see `skills/data-bus-fallback.md`).

**Single-quote every free-text argument that might contain a literal `$`** — `--rationale`/`--thesis`/`--thesis-claim`/`--thesis-invalidation`/`--close-reason`/`--note`/`--prediction-reason`/`--sector`. Double quotes do NOT stop shell `$` expansion — `"EPS $0.88"` in a real double-quoted shell string expands `$0` to the shell's own name and `$1`-`$9` to empty positional params, so it silently becomes `"EPS /bin/bash.88"` or `"EPS .88"` with the digit before the decimal just gone. Confirmed live 2026-08-10: this corrupted several `decisions.rationale`/`positions.thesis` rows with real dollar figures (EPS numbers, stop prices) — permanent, silent damage to the audit trail every time rationale text happens to include a price. Single quotes (`'...'`) are immune to this; use them by default for any of the fields above, not just when you notice a `$` in what you're about to write.

## Alpaca Executor

```bash
python3 scripts/executor.py --account stonks --action status
python3 scripts/executor.py --account stonks --action BUY --ticker SOFI --qty 2 --price 4.58 --conviction 0.6 --sector 'Consumer Tech'
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
| `bankroll` | cost ≤ current ceiling (`python3 bankroll.py`, backed by `state/trader.db`) | `--price` |
| `long_play` | (BUY, `--play-type long` only) size ≤ `risk.long_play.position_size_pct`; concurrent long plays < `risk.long_play.max_concurrent_long_plays` | `--play-type long --predicted-by-date YYYY-MM-DD --prediction-reason '...' --thesis-invalidation '...'` (all four required together) |
| `conviction_play` | (BUY, `--play-type conviction` only) size ≤ `risk.conviction_play.position_size_pct`; concurrent conviction plays < `risk.conviction_play.max_concurrent_conviction_plays` | `--play-type conviction --prediction-reason '...' --thesis-invalidation '...'` (all three required together) |

Missing field → gate skips (fail-open), never blocks on missing data. Always pass `--price` on SELL — it's what lets the bankroll ceiling adapt.

**Conviction/long-play thesis fields** (2026-08-03): `--thesis-claim` (the falsifiable directional claim) and `--thesis-invalidation` (the specific, checkable condition that would prove it wrong) are hard-required on any BUY tagged `--play-type long` or `--play-type conviction` — the order is rejected outright without both, same enforcement level as `--prediction-reason`. Optional (warn-only) for standard BUYs. Persisted to `state/trader.db`'s `positions.thesis_claim`/`thesis_invalidation`/`thesis_entry_signals` plus an append-only `position_thesis_log` history (a scale-in updates the current read but never loses the prior one). See `strategy.md`'s "Conviction-play eligibility anchor" for the judgment call on *when* to reach for `--play-type conviction` (`reconcile` agreement + signal_count≥3 + combined_confidence≥0.60) and "Daily thesis re-confirmation" for how an open conviction/long play gets re-checked (`trader_write.py position-update-thesis --verdict intact|weakening|broken`) instead of the per-tick market-context exit layer that applies to standard positions.

**`gate_status`** (2026-08-02, `--action status` output only): a fresh-computed, read-only snapshot of every "count vs cap" guardrail — `gate_status.sector_concentration.by_sector.<sector>` (`open`/`cap`/`at_cap`, mirrors `risk_guards.max_positions_per_sector`), `gate_status.daily_order_count` (`count_today`/`threshold`/`at_threshold`/`gate_enabled`, mirrors `risk_guards.order_count_audit_threshold_daily` + `guardrail_gates.order_count_audit`), `gate_status.max_positions` (`open_count`/`cap`/`gate_enabled`, mirrors `risk.max_positions` + `guardrail_gates.max_positions`). Computed from the exact same data/helpers the real gates above use — not a re-derived copy. Read this fresh every tick (tick_prompt.md step 4) and treat it as authoritative over anything recalled from a prior tick — a sector/limit that was "FULL" several ticks ago may not be anymore.

**Bankroll ceiling**: starts $50, +2%/win, -1%/loss (`scripts/bankroll.py`). Every SELL auto-records win/loss and recalculates — no separate call needed. Check anytime: `python3 bankroll.py`.

**Stop-loss scan** (tick_prompt.md step 6): `--action check-stops` returns positions past hard stop (`risk.stop_loss_pct`) or trailing stop (`risk.trailing_stop_pct`, ratchets up from peak since entry) — anything returned must be sold this tick, **except** `stop_type: "long_play_resolved"` (a long play's `predicted_by_date` arrived — informational, resolved automatically, not a sell signal; see `risk.long_play`). Toggle: `guardrail_gates.hard_stop` / `.trailing_stop` / `.long_play`.

## Decision Logging

**Mechanized as of 2026-08-02** — no separate call needed. The BUY/SELL executor call above already writes the `decisions` table row directly from the same `--conviction`/`--thesis`/`--features`/`--sector`/`--close-reason` you pass it, and SELL's win/loss outcome labeling is likewise automatic (`close_trade_outcome`). Writes to local `state/trader.db`'s `decisions` + `training_examples` tables — signal-level data for "which signal predicted wins."

`record_decision.py` still has one required standalone use — `reconcile`, to get `combined_confidence` for the `--conviction` you pass to the executor call (step 8 of tick_prompt.md):

```bash
python3 scripts/record_decision.py reconcile \
  --features '{"sentiment": {"direction": "bullish", "confidence": 0.7}, "technical": {"direction": "bullish", "confidence": 0.6}}'
```

`decision`/`close` subcommands still exist (manual/backfill invocation only — not a required per-trade step anymore):

```bash
python3 scripts/record_decision.py decision --ticker SOFI --action BUY --conviction 0.6 \
  --rationale '...' --regime momentum_bull --features '{...}'
python3 scripts/record_decision.py close --ticker SOFI --pnl 12.50 --return-pct 4.2
```
