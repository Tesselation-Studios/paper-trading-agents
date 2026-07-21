# Tick Prompt — Stonks (Consolidated MVP)

**Market tick received.** You are in your persistent trading session. Follow this loop:

## Core Loop

1. **Read active.md** → `read strategies/active.md` — this is your working memory from last tick. Know your last state.

2. **Read watchlist** → `read strategies/watchlist.md` — your growing/shrinking list of small-cap candidates. This is your discovery mechanism for this MVP (no ML, no news-source aggregation yet — just this).

3. **Check portfolio** → executor status check, see `skills/tool-invocation.md` — only source of truth for cash/positions/P&L, never the data bus.

4. **Market snapshot (best-effort)** → data bus per `skills/data-bus-fallback.md`; skip if stale/down, never block the tick.

5. **Scan positions near triggers** → run `python3 scripts/executor.py --account stonks --action check-stops` — mechanically checks every open position against the hard stop (`risk.stop_loss_pct`) and trailing stop (`risk.trailing_stop_pct`, ratchets up from peak price since entry). Any ticker returned in `breaches` **must** be sold this tick via step 8, no re-litigating. Also check profit targets, sentiment divergence, thesis breaks — read `positions/*.md` only for names near a trigger.

6. **Discovery pass (light touch)** → glance at the watchlist. Anything gone stale (idle_ticks over threshold in `params.json`)? Drop it. Noticed a new small-cap name worth watching (mentioned in sentiment scan, unusual volume, a name you'd have wanted a tick ago)? Add it with `idle_ticks: 0`. Keep this quick — this is not a full screen, just noticing.

7. **Decide** → BUY/SELL/HOLD with structured JSON, one entry per ticker considered. Keep rationale tight. Remember the mandate: **small-cap, wide and diverse** — many small positions over concentrated bets. Don't skip a good small opportunity just because you already hold a few names.

8. **Execute** → via executor (`skills/tool-invocation.md`) if trade — pass `--price`/`--conviction`/`--sector` so the executor's built-in guardrail check (position size, max positions, sector concentration, market hours, conviction floor) can evaluate it; a rejected order exits non-zero with the blocking gate's reason — do not retry the same trade, note it in active.md and move on. Update the position's thesis file if it changed. On any BUY/SELL (not routine HOLD), log the decision via `record_decision.py` (same skill) with per-signal features (sentiment/technical/regime — see `signals.py`), scored independently, not pre-blended. If the SELL closes a position, also log the outcome (pnl/return_pct from entry vs. exit).

9. **Update active.md** → append your current tick entry. Keep it **trim**:
   - 3-5 lines if no trade and no trigger event
   - Include: regime, portfolio value, position count, top/bottom movers (3 max each), positions near triggers, decision
   - Full P&L tables only when something actually changed — don't repeat the whole book every tick

10. **Git commit** → if you modified active.md, watchlist.md, or any thesis files, commit locally. See `skills/auto-commit.md`.

11. **HEARTBEAT_OK**

## Trim Rules

- No re-reading strategy.md or params.json — they're already in your session context.
- No P&L tables in the journal. That goes in active.md only, and only when something changed.
- If this tick is identical to last tick (same regime, no triggers, watchlist unchanged), write "Same as last tick" and done.
- Journal entry at EOD only (nightly maintenance), not per tick.

File map + tool syntax → `TOOLS.md`. Strategy/params → already in context, don't re-read.
