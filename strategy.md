# Stonks — Strategy stonks.strat:v1.3.1

## Philosophy

Trade small and wide, not concentrated — volume of decisions and honest feedback over a hero trade. Qualitative judgment that keeps proving out gets promoted into `params.json`/code. Promotion requires the backtest to actually hold up — v1.3 reverted two rounds of additions (v1.1, v1.2) that looked reasonable narratively but underperformed the simpler baseline on corrected data. See Version History.

## Current Approach

- **Universe**: $1–$50, small/mid-cap, reasonable liquidity. Starting constraint, not permanent — see Growth Trajectory.
- **Discovery**: `strategies/watchlist.md`, grow/shrink by notice/idle-ticks (`params.json: watchlist.idle_ticks_before_drop`). Decoupled from entry gating — `scripts/merge_discoveries.py` runs every tick regardless of regime, feeds probe-discovery output into the watchlist mechanically.
- **Sizing**: small per-position, many concurrent, diversification over conviction (`params.json: risk`).
- **Entry signal**: simple momentum — rising RSI in the 45-65 band + volume + a real catalyst. No triple-confirmation, no sector-ETF veto, no regime gate, no VIX-tiered sizing — all reverted in v1.3, see below.
- **Exit**: fixed stop-loss / profit-target from `params.json`, full exit (not partial trim), or thesis breaks. Never chase a peaked pump. Hard stop and trailing stop are mechanically enforced in `executor.py`'s guardrail gates regardless of strategy version — always on, not something this file's prose controls.
- **Pre-session GTC order audit**: clear all stale/unfilled GTC orders before first tick. Stale orders can silently block all exits.
- **Dual time horizon**: most positions short/fast, but some warrant a longer hold. Per-position qualitative call, not a fixed rule yet — let the pattern emerge in journal reflection first.

## Risk Management

- Per-ticker size cap, diversification enforced (`risk.max_position_pct`).
- Stops non-negotiable once triggered.
- No single position sized to wipe out several small wins.
- Max positions/sector + daily order-count audit (`risk_guards`).

## Growth Trajectory

Universe/sizing constraints are a starting point, not a ceiling. As real track record accumulates (win rate, portfolio growth — my own call, reassessed at nightly Evolve, never a hardcoded milestone), sanctioned to widen toward larger-cap names and larger sizes. Same "harden what's proven" bar as any other evolution.

## What I'm Learning

- One outsized loser wipes out several small winners — sizing > pick quality.
- High conviction ≠ high accuracy — keep calibrating.
- Stale pipeline is worse than no pipeline — fall back to price action honestly.
- Pre-market quotes are dealer indications, not price discovery — don't adjust conviction on them.
- **Superseded (see v1.3)**: "MACDh flip = most reliable near-term exit" and "regime-gating entries works" were codified in v1.1 from qualitative journal impressions, but a corrected 3-night backtest (fixed a MACD column-index bug that had been silently testing the wrong signal) showed v1.0's simple baseline outperforming v1.1 on raw return in 2 of 3 runs — the qualitative read didn't hold up empirically. Reverted, not deleted — keeping this note so the same claim doesn't get re-promoted without re-testing.
- Watchlist must be fed — 4 days with no new names means the pipeline's starved; 2-3/session minimum. Now mechanically enforced (`scripts/merge_discoveries.py`), not just a reminder.
- Low-confidence/CHOPPY: don't trust MACD divergence without price confirmation — it's a trap (Aldridge).
- Pre-session GTC order audit — clear all stale/unfilled GTC orders before first tick. Stale orders can silently block all exits (Jul 21 near-miss: 11 unfilled orders). Codified v1.2.1, kept in v1.3 — pure hygiene, not implicated in the v1.1/v1.2 underperformance.
- v1.2's triple-confirmation entry was too permissive — 170 trades vs 99 for v1.0 over the same 200d/22-ticker sample, worst return of any version across all 3 backtest nights. Reverted in v1.3, not just monitored — the data was consistent enough to act on.
- Pre-session account audit is as critical as GTC audit (Jul 22): shared credentials mean your Alpaca account may contain positions you don't recognize — audit against journal records symbol-by-symbol before first tick, or your position tracking is corrupt from the jump. Hardened v1.3.1.
- Strategy changes must be verified end-to-end across all layers (Jul 22): after revising `strategy.md`, explicitly check `params.json`, `executor.py`, and the tick agent's prompt for stale rules. v1.3's removal of the CHOPPY entry gate was missed for ~2 hours because the agent's mental model still carried v1.1/v1.2 gating logic. A strategy revision that only touches strategy.md is incomplete. Hardened v1.3.1 as deployment checklist.
- Trailing stop win rate is tracking low (Jul 22): 2W/4L (33%) across Jul 21-22 trail-stop exits. The 5% trail cuts losers cleanly, but entries aren't finding enough upside to clear the drag. Monitoring — if the ratio stays below 33% through 20 trail-stop exits, the entry criteria or stop parameters need work. Not a rule change yet, but flagged for data collection.

## Evolution Process
- Not limited to this file — evolve when something real is learned (nightly Step 3).
- Version `stonks.strat:v{major}.{minor}`; experimental `x-{name}` (5 trades, promote or revert).
- "Nothing changed" is valid.

## Version History

- `v1.3.1` (2026-07-22) — Hardened two operational patterns from Jul 21-22 incidents: (1) pre-session account audit — reconcile Alpaca positions against journal records symbol-by-symbol before first tick, same tier as GTC order audit (two different account hygiene failures in two consecutive days); (2) strategy deployment checklist — version bumps must sync across strategy.md → params.json → executor.py → agent prompt (Jul 22 lost 2 hours to v1.3 CHOPPY gate not reaching agent mental model). Flagged trailing stop win rate for monitoring: 2W/4L (33%) across Jul 21-22; if ratio holds below 33% through 20 trail-stop exits, revisit entry criteria or stop parameters.
- `v1.3` (2026-07-22) — Reverted to v1.0's entry/exit logic: simple RSI 45-65 momentum entry, fixed stop-loss/profit-target exit (full, not partial). Dropped triple-confirmation entry sizing, MACDh-flip hard exit, RSI-exhaustion hard exit, regime gate, VIX-tiered regime sizing, sector-ETF veto, quality gate, earnings blackout, and time-stop as strategy rules. Reason: a corrected `replay_check.py` (fixed a MACD column-index bug — every prior "MACDh flip" backtest was actually testing the signal line, not the histogram) showed v1.0 outperforming both v1.1 and v1.2 on raw return in 2 of 3 backtest nights, with v1.2 the clear worst on all 3 (see GitHub issue #3). Kept from v1.2.1: decoupled watchlist discovery (now mechanically enforced) and pre-session GTC order audit — pure process hygiene, not implicated in the underperformance. Mechanical guardrails (hard stop, trailing stop, position size, max positions, sector concentration, hours, conviction floor, bankroll ceiling) are unaffected — they live in `executor.py` now, independent of strategy version.
- `v1.2.1` (2026-07-21) — Hardened two patterns from 7-entry journal synthesis: (1) decoupled watchlist discovery from entry gating — scan and add candidates daily regardless of regime; (2) pre-session GTC order audit — clear stale orders before first tick. Flagged v1.2 triple-confirmation entry rules as under monitoring — two nights of corrected backtest data shows v1.2 underperforming v1.0 on entries (170 vs 99 trades, -3.05% vs -0.08% return).
- `v1.2` (2026-07-18) — Pulled pruned knowledge from retired Kairos (RSI exhaustion exit, triple-confirmation sizing, sector veto, earnings blackout, VIX-tiered sizing, sector/order-count guards, time-stop) + Aldridge (quality gate, trim discipline, CHOPPY lesson). Added Growth Trajectory + dual time-horizon. Excluded: Kairos's MACD-divergence-as-accumulation read (conflicts with v1.1's hard MACDh exit, already validated by replay_check.py); Aldridge's micro-cap sub-schema + Kairos's sub-$10/volume floors (conflict with small-cap mandate).
- `v1.1` (2026-07-18) — Hardened MACDh-flip exit + CHOPPY/FEAR entry gate from 5 days of journal patterns. Added watchlist-feeding discipline.
- `v1.0` (2026-07-17) — Consolidation MVP: reset from concentrated conviction plays to small-cap/wide/high-rep, onto Aldridge's tick-loop architecture. ML/fundamentals/news-aggregation deferred.
