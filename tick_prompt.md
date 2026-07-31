# Tick Prompt — Stonks (Consolidated MVP)

**Market tick received.** Each tick is a separately spawned session, not one long-lived warm conversation — nothing is "already in context" by default. Follow this loop:

## Core Loop

1. **Read strategy.md and params.json** → fresh, every tick, no exceptions. They're small; the read is cheap. Correctness > saving a few tokens.

   Before trading, run `python3 scripts/workspace_review.py --gate` (see `skills/workspace-review.md`). If it reports critical findings (`state/.workspace_blocked` set), **do not trade this tick** — note the reason in active.md and `HEARTBEAT_OK`. Warnings alone don't block; just carry them into active.md so they don't go unnoticed.

   Also call `memory_search("<today's regime> <top watchlist/position tickers>")` once — feeds the recall store dreaming promotes from. Skip only on tool error, never block the tick.

2. **Read active.md** → `read strategies/active.md` — this is your working memory from last tick. Know your last state.

3. **Read watchlist** → `python3 scripts/trader_query.py watchlist` — your growing/shrinking list of small-cap candidates. This is your discovery mechanism for this MVP (no ML, no news-source aggregation yet — just this).

4. **Check portfolio** → executor status check, see `skills/tool-invocation.md` — only source of truth for cash/positions/P&L, never the data bus. Its output now also includes a `deployment_pressure` block (cash_pct, consecutive under-deployed ticks, and research-escalation flags) — read once here, referenced by steps 7 and 8 below. Zero extra cost: it's computed from the same account fetch, not a second call.

5. **Market snapshot (best-effort)** → data bus per `skills/data-bus-fallback.md` for quotes/momentum/fear-greed; sentiment is separate — read `state/sentiment_cache.json` per `skills/sentiment-cache.md` (refreshed independently every ~15min, not fetched live per tick). Skip either if stale/down/missing, never block the tick.

   Also call `get_market_regime` and `get_risk` (MCP tools, not the REST curl) — real regime/risk data, not a price-action guess. Skip on error, same as everything else here.

   Once per day only (first tick after 09:30 ET, or if active.md shows no macro entry yet today): call `get_macro` too — yield curve/CPI/FOMC context doesn't change tick-to-tick, no need to re-fetch every 5 minutes.

6. **Scan positions near triggers** → run `python3 scripts/executor.py --account stonks --action check-stops` — mechanically checks every open position against the hard stop (`risk.stop_loss_pct`, fixed floor) and trailing stop (`risk.trailing_stop_pct`, ratchets up from peak price since entry). Also checks the oversized-position cap (`risk.max_position_pct` — a position that's grown over cap via price appreciation, not a new buy). Any ticker returned in `breaches` **must** be sold this tick via step 9, no re-litigating — for `stop_type: "oversized"`, sell exactly the `shares_to_sell` count given (a trim, not a full exit) unless another breach on the same ticker calls for a full exit instead. Also check profit targets, thesis breaks — for names near a trigger, `python3 scripts/trader_query.py positions --ticker <X>` to check the stored thesis.

   **`stop_type: "long_play_resolved"` is the one exception — not a sell signal.** It means a long play's `predicted_by_date` arrived (mechanically resolved right there in `check_stops()`, logged to training data automatically — nothing for you to do about the resolution itself). `long_play_hit`/`loss_pct` tell you whether the prediction was right. The position has now reverted to the normal trailing-stop schedule and should be judged like any standard HOLD/SELL decision from here — the breach itself doesn't obligate a sell.

   **Bootstrap-phase check**: `python3 scripts/trader_query.py bankroll` once — if `ceiling` is below `params.json: bootstrap_phase.ceiling_threshold`, apply the quick-exit bias (see strategy.md) to any open position sitting at or above `bootstrap_phase.quick_exit_min_return_pct`: bank it in step 9 rather than holding for the full profit target. Skip this check once ceiling clears the threshold.

7. **Discovery pass** → run `python3 scripts/merge_discoveries.py` unconditionally, every tick — mechanically merges any unconsumed `discoveries/*.md` candidates into the watchlist (idempotent, no-op if nothing new). Don't rely on remembering to do this manually; the script exists because that failed for days.

   Then run `python3 scripts/promote_candidates.py` unconditionally, every tick — pulls the top fresh (in-band, not stale) candidates out of the continuous `discovery_daemon.py` scanner's pool (`state/discovery_pool.db`, a real systemd-supervised background process, not a cron — always rotating through the tradable universe) into the watchlist, same dedup/`max_size` contract as the merge above. Idempotent, no-op if the pool has nothing new or fresh enough — zero real cost either way, this is a local SQLite read.

   Then check `python3 scripts/trader_query.py watchlist` — the merge above may not have refilled it. **If it's empty**, don't wait for the 45-min `stonks-discovery-urgent` cron: run `python3 scripts/discovery_urgency_check.py` right now. Its empty-pipeline check fires regardless of cash deployment, so it'll run a fresh scan and write straight to `discoveries/*.md` immediately — next tick's merge step picks the results up automatically. This is the only branch of step 7 that costs a real API call; skip it whenever Candidates already has entries — don't run it "just in case."

   Otherwise, light touch: anything gone stale (idle_ticks over threshold in `params.json`)? `python3 scripts/trader_write.py watchlist-drop-stale`. Noticed a new name worth watching from your own scan? `python3 scripts/trader_write.py watchlist-add --ticker X --note "..."` — the merge above only covers probe-discovery's output, not your own noticing.

   **Escalating research under sustained pressure**: step 4's `deployment_pressure` block also reports `escalate_freeform_research`/`escalate_backtest_check`. If `escalate_freeform_research` is true, run the `freeform-discovery` skill this tick in addition to its normal 8am cadence (news/catalyst hunting, not a trading action), then run `python3 deployment_pressure.py --mark-escalated freeform` so it doesn't refire every tick. If `escalate_backtest_check` is true, run `python3 scripts/replay_check.py` directly (same script `stonks-research-loop`'s nightly cron uses) and mark it with `--mark-escalated backtest`. Both are gated by persistence (~3hrs / ~1 trading day of sustained under-deployment) and their own cooldowns — don't run either "just in case."

8. **Decide** → BUY/SELL/HOLD with structured JSON, one entry per ticker considered. Keep rationale tight. Remember the mandate: **small-cap, wide and diverse** — many small positions over concentrated bets, no hard cap on position count (v1.6). **Doing nothing is a cost.** Before deciding HOLD, if `deployment_pressure.cash_pct` (step 4) is at or above `watchlist.discovery_urgency.cash_threshold_pct`, evaluate at least one probe-size (1-3 share) BUY before writing HOLD for the tick. This is "widen the funnel," not "force a trade regardless of gates" — the full gate chain (bankroll ceiling, sector concentration, portfolio risk, cash, hours) still runs unchanged, and a probe that gets correctly gated off is a **pass**, not a failure to fix. Log the attempt and why it was or wasn't gated in `active.md` either way, so a string of correctly-blocked probes stays visible rather than looking identical to not trying.

   **New-entry candidates are evaluated in a bounded rotation, not the whole watchlist every tick** — evaluating the full watchlist every tick blows the cron's execution timeout: use `python3 scripts/trader_query.py watchlist --batch N` (`params.json: watchlist.eval_batch_size`, most-neglected-first) instead of the full list from step 3/7 for this new-entry check. After deciding on that batch (BUY, gated, or nothing qualified — doesn't matter which), run `python3 scripts/trader_write.py watchlist-mark-evaluated --tickers <comma-separated batch tickers>` so the rotation advances — every candidate cycles through over several ticks instead of getting re-scanned from scratch each time. This bound applies only to *new*-entry watchlist screening; position management (step 6) and scaling-into-winners (below) stay exhaustive since the position list is naturally small.

   Also weigh **scaling into an existing winning position** (thesis intact, real momentum — not mechanically averaging up) as a real decision alongside new watchlist entries this tick, not a fallback only for when nothing new qualifies — reconsider it every tick it still qualifies, not just once, and size the add to how strong the winner actually is rather than a fixed probe increment (v1.7, see strategy.md's Scaling into Winners). **Exit**: a MACD histogram flip (positive→negative) triggers an immediate exit, in addition to the fixed stop-loss/profit-target (see step 6). Read strategy.md fresh each tick — don't carry forward last tick's rule set from memory.

   **Before entering any new position, reason over the gestalt, don't run a checklist.** Pull together: `get_insiders` (Form-4 filings), `get_fundamentals` (P/E, ROE, analyst target — `skills/fundamentals.md`), `get_congress` (Senate/House disclosures, last 1-2 weeks — silence means no recent disclosure, not "nothing to see"), `wiki_search` on both the ticker and its sector/theme (durable knowledge, yours and other agents', including `stonks-worldview-sync`'s standing sector/macro narratives — a per-ticker-only search misses those), and cross-sectional momentum rank (`skills/data-bus-fallback.md`'s `/momentum`). None of these individually gates a trade — RSI/volume/regime are signals to weigh, not a checklist to clear. The point is synthesis: a world narrative and a congressional trade pointing the same direction is stronger evidence than either alone, and worth saying so explicitly in the rationale. Skip `get_technical_scan` (paywalled) and `get_flow` (disabled — not real options flow data). These are heavier calls, so spend them on names you're actually about to buy, not the whole watchlist every tick. Skip any that error — none of these block a trade, they only inform it.

   **Conviction is a real score, not a guess**: score each signal you actually checked into `--features` JSON (per `skills/self-improving-agent.md`), then run `python3 scripts/record_decision.py reconcile --features '...'` and use its `combined_confidence` as the conviction you pass to both the executor call (step 9) and the post-trade `record_decision.py decision --conviction <same number>` call. `params.json: risk.conviction_floor` is a sanity check (catches a broken/zero score), not a bar to clear — real entry quality comes from the gestalt above, stated honestly in the rationale. You may still deviate (size up/down) if you have a concrete reason not captured in a scored signal — say why in `--rationale`; that disagreement is itself scorecard-relevant, same as the existing echoed-back `reconciled` framing already documents.

   **Two capital buckets, pick one per entry** (`params.json: risk.long_play` / `risk.conviction_play`):
   - **Standard**: the wide/fast opportunistic sweep, most of the book — no special tag needed.
   - **Conviction play**: a well-known/liquid name with an actual researched thesis from the gestalt above — larger size, wider trailing stop (`trail_multiplier`), held until the thesis plays out or breaks rather than a mechanical exit. Pass `--play-type conviction --prediction-reason "..."` on the BUY, citing the specific evidence (not "seems fine, let's wait and see").
   - **Long play**: small, short-horizon (`horizon_days`, currently 3), evidence-gated — an explicit prediction with a resolution date, not open-ended patience. Pass `--play-type long --predicted-by-date YYYY-MM-DD --prediction-reason "..."`. Do not propose a longer horizon yourself — that only changes after Raf reviews hit rate via `signal_scorecard.py --label-horizon long_play_prediction`.
   
   Both play types still have the hard stop apply unconditionally. `gate_long_play`/conviction sizing enforce the caps; a rejection just means fall back to standard, don't retry. Include the play-type tag in the same `--features` JSON passed to `record_decision.py decision`.

9. **Execute** → via executor (`skills/tool-invocation.md`) if trade — pass `--price`/`--conviction`/`--sector` and `--thesis` (reuse the same rationale you're about to log with `record_decision.py`, no need to compose it twice) so the executor's built-in guardrail check (position size, max positions, sector concentration, market hours, conviction floor, bankroll ceiling) can evaluate it; a rejected order exits non-zero with the blocking gate's reason — do not retry the same trade, note it in active.md and move on. For a SELL, also pass `--close-reason` (step 6's breach reason, a thesis break, or a profit-target/MACDh exit — whatever actually triggered it). On any BUY/SELL (not routine HOLD), log the decision via `record_decision.py decision` (same skill) with the same `--conviction`/`--features` from step 8's reconcile call — see `skills/self-improving-agent.md` for exactly how to score each signal and what the echoed-back `reconciled` result means. If the SELL closes a position, also log the outcome (pnl/return_pct from entry vs. exit).

   For a long play (step 8), also pass `--play-type long --predicted-by-date YYYY-MM-DD --prediction-reason "..."` on the BUY — all three are required together or the executor rejects it outright. Include the long-play tag (`"play_type": "long"`, `"predicted_by_date": "..."`) in the same `--features` JSON you pass to `record_decision.py decision`, so it's on the training_examples row for later review.

   **Position thesis/sector persistence is automatic**: a BUY writes sector/thesis straight into `state/trader.db`'s `positions` row at open time; a SELL closes or trims that same row via the executor call above — no separate file to maintain, nothing to remember to update. For a standalone reasoning update not tied to a trade this tick (stop-loss note, conviction shift, catalyst development on a name you're not acting on right now), run `python3 scripts/trader_write.py position-update-thesis --ticker X --thesis "..."` directly.

   **Writing durable knowledge to the wiki**: `positions.thesis`/`active.md` are this tick's working state, not durable knowledge other sessions or agents can find later. When you reach a real, evidence-backed conclusion worth persisting beyond this tick — a sector thesis, a pattern you've now seen confirmed a few times, something worth not re-deriving from scratch next time — write it with `wiki_apply synthesis "<title>" --body "..." --source-id <id>`, citing the specific evidence it's based on (same discipline as the `--rationale`/`--features` citations above, not just prose). This is occasional, not every tick or every trade. Run `wiki_lint` after writing to catch structural gaps.

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
