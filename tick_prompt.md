# Tick Prompt — Stonks (Consolidated MVP)

**Market tick received.** Each tick is a separately spawned session, not one long-lived warm conversation — nothing is "already in context" by default. Follow this loop:

## Core Loop

1. **Read strategy.md and params.json** → fresh, every tick, no exceptions. Confirmed 2026-07-22: a same-day strategy revert (v1.2.1→v1.3, dropping the regime gate) didn't take effect for several ticks because this step was previously skipped on the wrong assumption these files were already warm in context. They're small; the read is cheap. Correctness > saving a few tokens.

   Before trading, run `python3 scripts/workspace_review.py --gate` (see `skills/workspace-review.md`). If it reports critical findings (`state/.workspace_blocked` set), **do not trade this tick** — note the reason in active.md and `HEARTBEAT_OK`. Warnings alone don't block; just carry them into active.md so they don't go unnoticed (this is exactly how the Jul 22 CHOPPY-gate lag went undetected for 2 hours).

   Also call `memory_search("<today's regime> <top watchlist/position tickers>")` once — feeds the recall store dreaming promotes from. Skip only on tool error, never block the tick.

2. **Read active.md** → `read strategies/active.md` — this is your working memory from last tick. Know your last state.

3. **Read watchlist** → `read strategies/watchlist.md` — your growing/shrinking list of small-cap candidates. This is your discovery mechanism for this MVP (no ML, no news-source aggregation yet — just this).

4. **Check portfolio** → executor status check, see `skills/tool-invocation.md` — only source of truth for cash/positions/P&L, never the data bus.

5. **Market snapshot (best-effort)** → data bus per `skills/data-bus-fallback.md` for quotes/momentum/fear-greed; sentiment is separate — read `state/sentiment_cache.json` per `skills/sentiment-cache.md` (refreshed independently every ~15min, not fetched live per tick). Skip either if stale/down/missing, never block the tick.

   Also call `get_market_regime` and `get_risk` (MCP tools, not the REST curl) — real regime/risk data, not a price-action guess. These were built and available the whole time but never actually called; use them now instead of inferring regime from raw price action alone. Skip on error, same as everything else here.

   Once per day only (first tick after 09:30 ET, or if active.md shows no macro entry yet today): call `get_macro` too — yield curve/CPI/FOMC context doesn't change tick-to-tick, no need to re-fetch every 5 minutes.

6. **Scan positions near triggers** → run `python3 scripts/executor.py --account stonks --action check-stops` — mechanically checks every open position against the hard stop (`risk.stop_loss_pct`), trailing stop (`risk.trailing_stop_pct`, ratchets up from peak price since entry), and oversized-position cap (`risk.max_position_pct` — a position that's grown over cap via price appreciation, not a new buy). Any ticker returned in `breaches` **must** be sold this tick via step 9, no re-litigating — for `stop_type: "oversized"`, sell exactly the `shares_to_sell` count given (a trim, not a full exit) unless another breach on the same ticker calls for a full exit instead. Also check profit targets, thesis breaks — read `positions/*.md` only for names near a trigger.

7. **Discovery pass** → run `python3 scripts/merge_discoveries.py` unconditionally, every tick — mechanically merges any unconsumed `discoveries/*.md` candidates into `watchlist.md` (idempotent, no-op if nothing new). Don't rely on remembering to do this manually; the script exists because that failed for days. Then, light touch: glance at the watchlist — anything gone stale (idle_ticks over threshold in `params.json`)? Drop it. Noticed a new name worth watching from your own scan? Add it too — the script only covers probe-discovery's output, not your own noticing.

8. **Decide** → BUY/SELL/HOLD with structured JSON, one entry per ticker considered. Keep rationale tight. Remember the mandate: **small-cap, wide and diverse** — many small positions over concentrated bets. Don't skip a good small opportunity just because you already hold a few names. Per `strategy.md` v1.4 (2026-07-23, re-promoted from v1.1): RSI 45-65 momentum entry, **skipped when 20-day realized volatility is elevated** (regime gate is back — this reverses what v1.3 said). **Exit**: a MACD histogram flip (positive→negative) triggers an immediate exit, in addition to the fixed stop-loss/profit-target. Read strategy.md fresh each tick — don't carry forward last tick's rule set from memory.

   Before entering any new position specifically (not for HOLD/routine ticks): call `get_technical_scan` on the candidate, plus `get_flow` (options flow) and `get_insiders` (Form-4 filings) as a conviction check, and `get_fundamentals` for valuation context (P/E, ROE, analyst target — see `skills/fundamentals.md`). These are heavier calls, so only spend them on names you're actually about to buy, not the whole watchlist every tick. Fold whatever they show into the conviction score passed to the executor. Skip any that error — none of these block a trade, they only inform it.

9. **Execute** → via executor (`skills/tool-invocation.md`) if trade — pass `--price`/`--conviction`/`--sector` so the executor's built-in guardrail check (position size, max positions, sector concentration, market hours, conviction floor, bankroll ceiling) can evaluate it; a rejected order exits non-zero with the blocking gate's reason — do not retry the same trade, note it in active.md and move on. Update the position's thesis file if it changed. On any BUY/SELL (not routine HOLD), log the decision via `record_decision.py` (same skill) with per-signal features (sentiment/technical/regime — see `signals.py`), scored independently, not pre-blended. If the SELL closes a position, also log the outcome (pnl/return_pct from entry vs. exit).

10. **Update active.md** → append your current tick entry. Keep it **trim**:
   - 3-5 lines if no trade and no trigger event
   - Include: regime, portfolio value, position count, top/bottom movers (3 max each), positions near triggers, decision
   - Full P&L tables only when something actually changed — don't repeat the whole book every tick

11. **Git commit** → if you modified active.md, watchlist.md, or any thesis files, commit locally. See `skills/auto-commit.md`.

12. **HEARTBEAT_OK**

## Trim Rules

- No P&L tables in the journal. That goes in active.md only, and only when something changed.
- If this tick is identical to last tick (same regime, no triggers, watchlist unchanged), write "Same as last tick" and done.
- Journal entry at EOD only (nightly maintenance), not per tick.

File map + tool syntax → `TOOLS.md`. Strategy/params → read fresh every tick per step 1, never assumed warm.
