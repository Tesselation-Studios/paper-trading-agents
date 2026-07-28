# Tick Prompt — Stonks (Consolidated MVP)

**Market tick received.** Each tick is a separately spawned session, not one long-lived warm conversation — nothing is "already in context" by default. Follow this loop:

## Core Loop

1. **Read strategy.md and params.json** → fresh, every tick, no exceptions. They're small; the read is cheap. Correctness > saving a few tokens.

   Before trading, run `python3 scripts/workspace_review.py --gate` (see `skills/workspace-review.md`). If it reports critical findings (`state/.workspace_blocked` set), **do not trade this tick** — note the reason in active.md and `HEARTBEAT_OK`. Warnings alone don't block; just carry them into active.md so they don't go unnoticed.

   Also call `memory_search("<today's regime> <top watchlist/position tickers>")` once — feeds the recall store dreaming promotes from. Skip only on tool error, never block the tick.

2. **Read active.md** → `read strategies/active.md` — this is your working memory from last tick. Know your last state.

3. **Read watchlist** → `python3 scripts/trader_query.py watchlist` — your growing/shrinking list of small-cap candidates. This is your discovery mechanism for this MVP (no ML, no news-source aggregation yet — just this).

4. **Check portfolio** → executor status check, see `skills/tool-invocation.md` — only source of truth for cash/positions/P&L, never the data bus. Its output now also includes a `deployment_pressure` block (cash_pct, consecutive under-deployed ticks, the *effective* conviction floor for this tick, and research-escalation flags) — read once here, referenced by steps 7 and 8 below. Zero extra cost: it's computed from the same account fetch, not a second call.

5. **Market snapshot (best-effort)** → data bus per `skills/data-bus-fallback.md` for quotes/momentum/fear-greed; sentiment is separate — read `state/sentiment_cache.json` per `skills/sentiment-cache.md` (refreshed independently every ~15min, not fetched live per tick). Skip either if stale/down/missing, never block the tick.

   Also call `get_market_regime` and `get_risk` (MCP tools, not the REST curl) — real regime/risk data, not a price-action guess. Skip on error, same as everything else here.

   Once per day only (first tick after 09:30 ET, or if active.md shows no macro entry yet today): call `get_macro` too — yield curve/CPI/FOMC context doesn't change tick-to-tick, no need to re-fetch every 5 minutes.

6. **Scan positions near triggers** → run `python3 scripts/executor.py --account stonks --action check-stops` — mechanically checks every open position against the hard stop (`risk.stop_loss_pct`, fixed floor) and trailing stop (`risk.trailing_stop_pct`, ratchets up from peak price since entry). Also checks the oversized-position cap (`risk.max_position_pct` — a position that's grown over cap via price appreciation, not a new buy). Any ticker returned in `breaches` **must** be sold this tick via step 9, no re-litigating — for `stop_type: "oversized"`, sell exactly the `shares_to_sell` count given (a trim, not a full exit) unless another breach on the same ticker calls for a full exit instead. Also check profit targets, thesis breaks — for names near a trigger, `python3 scripts/trader_query.py positions --ticker <X>` to check the stored thesis.

7. **Discovery pass** → run `python3 scripts/merge_discoveries.py` unconditionally, every tick — mechanically merges any unconsumed `discoveries/*.md` candidates into the watchlist (idempotent, no-op if nothing new). Don't rely on remembering to do this manually; the script exists because that failed for days.

   Then run `python3 scripts/promote_candidates.py` unconditionally, every tick — pulls the top fresh (in-band, not stale) candidates out of the continuous `discovery_daemon.py` scanner's pool (`state/discovery_pool.db`, a real systemd-supervised background process, not a cron — always rotating through the tradable universe) into the watchlist, same dedup/`max_size` contract as the merge above. Idempotent, no-op if the pool has nothing new or fresh enough — zero real cost either way, this is a local SQLite read.

   Then check `python3 scripts/trader_query.py watchlist` — the merge above may not have refilled it. **If it's empty**, don't wait for the 45-min `stonks-discovery-urgent` cron: run `python3 scripts/discovery_urgency_check.py` right now. Its empty-pipeline check fires regardless of cash deployment, so it'll run a fresh scan and write straight to `discoveries/*.md` immediately — next tick's merge step picks the results up automatically. This is the only branch of step 7 that costs a real API call; skip it whenever Candidates already has entries — don't run it "just in case."

   Otherwise, light touch: anything gone stale (idle_ticks over threshold in `params.json`)? `python3 scripts/trader_write.py watchlist-drop-stale`. Noticed a new name worth watching from your own scan? `python3 scripts/trader_write.py watchlist-add --ticker X --note "..."` — the merge above only covers probe-discovery's output, not your own noticing.

   **Escalating research under sustained pressure**: step 4's `deployment_pressure` block also reports `escalate_freeform_research`/`escalate_backtest_check`. If `escalate_freeform_research` is true, run the `freeform-discovery` skill this tick in addition to its normal 8am cadence (news/catalyst hunting, not a trading action), then run `python3 deployment_pressure.py --mark-escalated freeform` so it doesn't refire every tick. If `escalate_backtest_check` is true, run `python3 scripts/replay_check.py` directly (same script `stonks-research-loop`'s nightly cron uses) and mark it with `--mark-escalated backtest`. Both are gated by persistence (~3hrs / ~1 trading day of sustained under-deployment) and their own cooldowns — don't run either "just in case."

8. **Decide** → BUY/SELL/HOLD with structured JSON, one entry per ticker considered. Keep rationale tight. Remember the mandate: **small-cap, wide and diverse** — many small positions over concentrated bets, no hard cap on position count (v1.6). **Doing nothing is a cost.** Before deciding HOLD, if `deployment_pressure.cash_pct` (step 4) is at or above `watchlist.discovery_urgency.cash_threshold_pct`, evaluate at least one probe-size (1-3 share) BUY against `deployment_pressure.effective_conviction_floor` (the *dynamic* floor from step 4, not the flat `params.json` number) before writing HOLD for the tick. This is "widen the funnel, keep a floor," not "force a trade regardless of gates" — the full gate chain (bankroll ceiling, sector concentration, position count, portfolio risk, cash, hours) still runs unchanged, and a probe that gets correctly gated off is a **pass**, not a failure to fix. Log the attempt and why it was or wasn't gated in `active.md` either way, so a string of correctly-blocked probes stays visible rather than looking identical to not trying. A qualifying watchlist candidate (RSI-in-band, volume-confirmed) is a real entry, sized by regime (`get_market_regime`: probe 1-3 shares if uncertain, normal if clear) rather than skipped (v1.5). Also weigh **scaling into an existing winning position** (thesis intact, real momentum — not mechanically averaging up) as a real decision alongside new watchlist entries this tick, not a fallback only for when nothing new qualifies — reconsider it every tick it still qualifies, not just once, and size the add to how strong the winner actually is rather than a fixed probe increment (v1.7, see strategy.md's Scaling into Winners). **Exit**: a MACD histogram flip (positive→negative) triggers an immediate exit, in addition to the fixed stop-loss/profit-target (see step 6). Read strategy.md fresh each tick — don't carry forward last tick's rule set from memory.

   Before entering any new position specifically (not for HOLD/routine ticks): call `get_flow` (options flow) and `get_insiders` (Form-4 filings) as a conviction check, and `get_fundamentals` for valuation context (P/E, ROE, analyst target — see `skills/fundamentals.md`). Skip `get_technical_scan` — permanently paywalled on LoneStarOracle's free tier. These are heavier calls, so only spend them on names you're actually about to buy, not the whole watchlist every tick. Fold whatever they show into the signal features you score below. Skip any that error — none of these block a trade, they only inform it.

   **Conviction is now a real score, not a guess**: score each signal you actually checked into `--features` JSON (per `skills/self-improving-agent.md`), then run `python3 scripts/record_decision.py reconcile --features '...'` and use its `combined_confidence` as the conviction you pass to both the executor call (step 9) and the post-trade `record_decision.py decision --conviction <same number>` call. You may still deviate (size up/down) if you have a concrete reason not captured in a scored signal — say why in `--rationale`; that disagreement is itself scorecard-relevant, same as the existing echoed-back `reconciled` framing already documents.

9. **Execute** → via executor (`skills/tool-invocation.md`) if trade — pass `--price`/`--conviction`/`--sector` and `--thesis` (reuse the same rationale you're about to log with `record_decision.py`, no need to compose it twice) so the executor's built-in guardrail check (position size, max positions, sector concentration, market hours, conviction floor, bankroll ceiling) can evaluate it; a rejected order exits non-zero with the blocking gate's reason — do not retry the same trade, note it in active.md and move on. For a SELL, also pass `--close-reason` (step 6's breach reason, a thesis break, or a profit-target/MACDh exit — whatever actually triggered it). On any BUY/SELL (not routine HOLD), log the decision via `record_decision.py decision` (same skill) with the same `--conviction`/`--features` from step 8's reconcile call — see `skills/self-improving-agent.md` for exactly how to score each signal and what the echoed-back `reconciled` result means. If the SELL closes a position, also log the outcome (pnl/return_pct from entry vs. exit).

   **Position thesis/sector persistence is automatic** (2026-07-28): a BUY writes sector/thesis straight into `state/trader.db`'s `positions` row at open time; a SELL closes or trims that same row via the executor call above — no separate file to maintain, nothing to remember to update. For a standalone reasoning update not tied to a trade this tick (stop-loss note, conviction shift, catalyst development on a name you're not acting on right now), run `python3 scripts/trader_write.py position-update-thesis --ticker X --thesis "..."` directly.

10. **Update active.md** → append your current tick entry. Keep it **trim**:
   - 3-5 lines if no trade and no trigger event
   - Include: regime, portfolio value, position count, top/bottom movers (3 max each), positions near triggers, decision
   - Full P&L tables only when something actually changed — don't repeat the whole book every tick

11. **Git commit** → if you modified active.md, commit locally. See `skills/auto-commit.md`.

12. **HEARTBEAT_OK**

## Trim Rules

- No P&L tables in the journal. That goes in active.md only, and only when something changed.
- If this tick is identical to last tick (same regime, no triggers, watchlist unchanged), write "Same as last tick" and done.
- Journal entry at EOD only (nightly maintenance), not per tick.

File map + tool syntax → `TOOLS.md`. Strategy/params → read fresh every tick per step 1, never assumed warm.
