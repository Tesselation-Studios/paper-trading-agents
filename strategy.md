# Stonks — Strategy stonks.strat:v1.23

General philosophy and strategy. **Specific, situational rules — the ones with a concrete trigger and a BUY/SELL/HOLD leaf — live in `decision_heuristics.md`, not here.** This file is what Stan reasons *from*; the tree is what Stan checks *first*, before reasoning. Read both fresh every tick (`tick_prompt.md` step 1).

## Philosophy

Trade small and wide, not concentrated — volume of decisions and honest feedback over a hero trade. Qualitative judgment that keeps proving out gets promoted into `params.json`/code, or into a `decision_heuristics.md` node once it's specific and repeatable enough to state as a trigger → action. Promotion requires split-window Sharpe positive in both halves independently, not aggregate return.

**Doing nothing is a cost, not a safe default.** A quiet book is a failure mode. No action needed on current positions is the cue to go find a new one, not to coast.

**Entry is a judgment call, not a formula**: RSI, volume, momentum, and regime are signals to weigh, not a checklist. Reason over the whole gestalt for a name — world/sector narrative, congressional trades, fundamentals, cross-sectional momentum rank, technicals — and state real conviction in the rationale. Two signals pointing the same direction is stronger evidence than either alone; say so. `conviction_floor` in `params.json` is a sanity check, not a threshold to clear. No catalyst requirement, no triple-confirmation, no sector-ETF veto, no VIX-tiered sizing. `decision_heuristics.md`'s `Active` nodes are a fast-path prior for situations this gestalt has already resolved cleanly and repeatedly — check them first, but a clean match is an override-able default, not a substitute for this reasoning when nothing cleanly matches.

**Sizing**: small per-position, many concurrent, diversification over conviction — no hard cap on position count, cash/`max_position_pct`/the bankroll ceiling are the real limiters. How large a *given* position should be is a conviction-tier question, resolved by `decision_heuristics.md`'s tier system (`params.json: risk.max_position_pct`/`risk.conviction_play.position_size_pct`) — not restated here.

**Two capital buckets** (`params.json: risk.long_play` / `risk.conviction_play`): most of the book is the wide/fast opportunistic sweep above — standard sizing, no special tag. A smaller conviction bucket is for well-known/liquid names with an actual researched thesis, sized larger, held until the thesis plays out or breaks rather than a mechanical exit — never exempt from the hard stop. Long plays are a separate, smaller, evidence-gated short-horizon exception (explicit prediction + resolution date). A subset of the conviction bucket may hold broad-market index ETFs as a cash-deployment anchor. Eligibility, sizing, and exit specifics for both buckets (including index-anchors) are `decision_heuristics.md` nodes, not restated here.

**Exit**: the hard stop-loss and (volatility-scaled) trailing stop live in `params.json: risk` and are mechanically enforced in `executor.py`'s `check_stops()` — not a judgment call. `profit_target_pct` is a guide only (`risk.profit_target_is_guide: true`) referenced by the offline backtest harness (`replay_check.py`), not a live sell trigger — nothing in the live path force-exits a winner at a target price. **Hold winners as long as the trailing stop hasn't been hit** (Raf's direction, 2026-08-12): a real trend has no natural ceiling worth capping by hand, so let the trailing stop — which ratchets up with price, not a fixed level — be the only thing that takes profit on a winner. Which *situational* exit call to make (peaked-pump restraint, market-context trim, thesis break) is `decision_heuristics.md` territory.

**Cash floor**: keep at least `risk.min_cash_reserve` on hand at all times (2026-08-12, Raf's direction) — mechanically enforced by `gate_cash`, not a judgment call. This is a floor for opportunistic capacity (something better shows up), not a target to sit at; deployment pressure still applies above the floor.

**Dual time horizon**: most positions short/fast, some warrant a longer hold. Per-position qualitative call.

**Pre-session GTC order audit**: clear all stale/unfilled GTC orders before first tick.

## Risk Management

- Per-ticker size cap, diversification enforced (`risk.max_position_pct`).
- Stops non-negotiable once triggered.
- No single position sized to wipe out several small wins.
- Max positions/sector + daily order-count audit (`risk_guards`).

## Growth Trajectory

Universe/sizing constraints are a starting point, not a ceiling. As real track record accumulates (win rate, portfolio growth — reassessed at nightly Evolve, never a hardcoded milestone), sanctioned to widen toward larger-cap names and larger sizes. Same "harden what's proven" bar as any other evolution.

## What I'm Learning

- Daily order-count guardrail (`risk_guards.order_count_audit_threshold_daily`, 30) is a self-imposed rogue-loop backstop, not an Alpaca limit — Alpaca's actual constraint is a 200 req/min API rate cap.
- `order_idempotency: true` (`params.json`) prevents parallel-tick duplicate-order collisions.
- One outsized loser wipes out several small winners — sizing > pick quality.
- High conviction ≠ high accuracy — keep calibrating.
- Stale pipeline is worse than no pipeline — fall back to price action honestly.
- Pre-market quotes are dealer indications, not price discovery — don't adjust conviction on them.
- Promotion bar is split-window Sharpe (both halves positive independently), not raw aggregate return.
- Watchlist must be fed — 2-3 new names/session minimum. Mechanically enforced end to end: `scripts/discovery_scan.py` generates candidates, `scripts/merge_discoveries.py` feeds them into the watchlist every tick, `scripts/discovery_urgency_check.py` forces an immediate scan whenever the candidate pipeline goes empty (checked every tick), plus a 45-min cron whenever cash sits mostly idle with a thin pipeline.
- Low-confidence regime read (or `mean_reversion`/`volatility_spike`): don't trust MACD divergence without price confirmation — it's a trap.
- Near-zero MACDh oscillation (±0.003 range on a flat price) is noise, not a signal — don't exit. Real flips are 4-5 bar declining trends with price confirmation.
- Pre-session account audit: audit against journal records symbol-by-symbol before first tick.
- Strategy changes must be verified end-to-end: after revising `strategy.md`, check `params.json`, `executor.py`, `tick_prompt.md`, and `decision_heuristics.md` for stale rules.
- v1.20 (2026-08-05): scale-into-winners (v1.7 addition) was actively destructive — degraded v1.1's Sharpe 4.618 → 2.099, first-half near-flat. Reverted to v1.1 core. Stop/target tightened (-10%,12%) → (-6%,10%) per split-window sweep: Sharpe 3.982 vs 3.148, higher-rep pattern (198 vs 124 trades).
- v1.21 (2026-08-10): split the file in two. Situational, trigger→action rules (peaked-pump restraint, market-context exit, catalyst-liquidity gate, conviction-play/index-anchor eligibility and exits) moved out of this file's prose and into `decision_heuristics.md` as real nodes with a BUY/SELL/HOLD leaf each — this file keeps only general philosophy/strategy. Nothing about the underlying rules changed, only where they're written and how fast they're checked.
- v1.22 (2026-08-11): stop-loss widened -6% → -10% per sweep evidence (`proposals/2026-08-11-widen-stop-loss-from-6-to-10-per-sweep-evidence.md`, Sharpe 1.431 vs 1.068, robust both halves). `params.json` was updated same day; this changelog entry was missed at the time — added retroactively 2026-08-12 while auditing for drift.
- v1.23 (2026-08-12): hold winners as long as the trailing stop hasn't hit — no hand-capped profit target (Raf's direction; corrected a stale claim that `profit_target_pct` was mechanically enforced — it never was, `replay_check.py`-only). New cash floor (`risk.min_cash_reserve`, `gate_cash`), same direction.

## Evolution Process

- Not limited to this file — evolve when something real is learned (nightly Step 3). A specific, repeatable trigger→action rule belongs in `decision_heuristics.md` (see `skills/decision-tree.md`'s lifecycle), not as a new bullet here — this file is for philosophy that doesn't reduce to a clean trigger.
- Version `stonks.strat:v{major}.{minor}`; experimental `x-{name}` (5 trades, promote or revert).
- "Nothing changed" is valid.
- Run `skills/rule-mechanization-audit.md` every cycle — surfaces which prose rules here are repeatedly violated (mechanize candidates) vs. no longer relevant (removal candidates), and checks `decision_heuristics.md` nodes for drift against the rule they were split out from.
- No Version History section here — git tracks it. Write rationale into the commit message when bumping the version (`git log strategy.md` to read it back).
