# Stonks — Strategy stonks.strat:v1.16

## Philosophy

Trade small and wide, not concentrated — volume of decisions and honest feedback over a hero trade. Qualitative judgment that keeps proving out gets promoted into `params.json`/code. Promotion requires the backtest to actually hold up — split-window Sharpe (positive in BOTH halves of the window independently), not a single aggregate-return number. Full rationale for any version change lives in the git commit that made it, not here — `git log strategy.md`.

**Doing nothing is a cost, not a safe default.** A quiet book is a failure mode. No action needed on current positions is the cue to go find a new one, not to coast.

## Current Approach

- **Universe**: $1–$500 (widened 2026-07-24 on real backtest evidence — small-cap-only underperformed mid-cap on this exact strategy, see `bankroll.py`'s `UNIVERSE_MAX_PRICE_TIERS`), reasonable liquidity, bankroll-scaled max price grows further from there — see Growth Trajectory.
- **Discovery**: `state/trader.db`'s watchlist_candidates table, grow/shrink by notice/idle-ticks (`params.json: watchlist.idle_ticks_before_drop`). Decoupled from entry gating — `scripts/merge_discoveries.py` runs every tick regardless of regime, feeds probe-discovery output into the watchlist mechanically.
- **Sizing**: small per-position, many concurrent, diversification over conviction (`params.json: risk`). 1-3 shares is a legitimate position, not a lesser one — probe size when conviction/regime is uncertain, normal size when clear. No hard cap on position count (removed 2026-07-24) — cash, `max_position_pct`, and the bankroll ceiling are the real limiters, not an arbitrary ticker-count gate.
- **Entry is a judgment call, not a formula**: RSI, volume, momentum, and regime are signals to weigh, not a checklist a candidate must clear before consideration. Reason over the whole gestalt for a name — world/sector narrative (wiki, `stonks-worldview-sync`), congressional trades (`get_congress`), fundamentals (`get_fundamentals`/`get_insiders`), cross-sectional momentum rank, technicals — and state real conviction in the rationale. Two signals pointing the same direction is stronger evidence than either alone; say so. `conviction_floor` in `params.json` is a sanity check now (catches a broken/zero score), not a threshold to clear. No catalyst requirement, no triple-confirmation, no sector-ETF veto, no VIX-tiered sizing.
- **Two capital buckets** (`params.json: risk.long_play` / `risk.conviction_play`): most of the book is the wide/fast opportunistic sweep above. A smaller conviction bucket is for well-known/liquid names with an actual researched thesis — larger size, wider trailing stop (still not exempt from it, and never from the hard stop), held until the thesis plays out or breaks rather than a mechanical exit. Long plays are a separate, smaller, evidence-gated short-horizon exception (explicit prediction + resolution date) — don't conflate the two.
- **Exit**: Hard stop-loss (`risk.stop_loss_pct`, currently -10%) is a non-negotiable full exit. Volatility-scaled trailing stop ratchets up from peak price (TRAIL_K=40, clamped 4-12% based on 20d realized vol — wider for noisier tickers, tighter for stable ones). Profit-target (`risk.profit_target_pct`) is a **guide, not an automatic trigger** — near it, use judgment. Also exit on thesis breaks. MACDh-flip is NO LONGER a mandatory exit (v1.13 — removed 2026-07-30: backtest evidence shows v1.0 without it beats all versions with it). Never chase a peaked pump — on a fresh MACDh flip, if RSI is already extended (>65 bullish / <35 bearish) and price has already run meaningfully (>1%) in the flip direction, treat that as the peaked-pump pattern: require an additional independent confirming signal before entering, size down, or wait for a pullback. Graduated, not a hard cutoff — a fresh flip at RSI 64 isn't meaningfully different from one at 66 (2026-08-02 controlled experiment: `state/experiments/peak-entry-discipline.jsonl`, see git log for this line). All stops are mechanically enforced in `executor.py`'s guardrail gates.
- **Market-context exit discipline** (higher-priority judgment layer, on top of the position's own stop/target): a position's own trailing stop and profit-target only react to *that position's* price action — they don't see a broad-market fade coming. If an open position is up meaningfully (roughly >2% intraday) and SPY's own trend turns — RSI drops materially (>8pts) or MACDh flips/declines meaningfully — treat that as real risk to giving the gain back and take profits (trim or close) rather than holding for the full target on the position's signals alone. Measured bigger than the peaked-pump entry heuristic above (2026-08-02 controlled experiment, 4 days: +$26.46 vs +$2.74 — see `state/experiments/exit-discipline.jsonl`), including a clean stress-test pass on a genuine uptrend day (zero false positives — didn't fire on a winner that kept running). Judgment heuristic, not a mechanical gate — same reasoning as above on why this isn't hardcoded into `params.json` yet.
- **Bootstrap-phase profit-taking bias** (2026-07-28, `params.json: bootstrap_phase`): while bankroll's ceiling is still below `ceiling_threshold`, tilt the profit-target judgment above toward taking a clearly-positive win (past `quick_exit_min_return_pct`) rather than holding for the full target — ceiling growth is win-*count*-driven (any `pnl > 0` close, regardless of size, per `bankroll.py`'s `recalc_ceiling`), so banking more small real wins compounds capital-to-trade-with faster while the account is small. This is a deliberate early-account tactic, not the long-run approach — as ceiling grows past the threshold, revert fully to the let-winners-run judgment above. Still never sell at a loss to force a "win," and thesis breaks/stops are unaffected — this only shifts the bias on genuinely profitable exits.
- **Pre-session GTC order audit**: clear all stale/unfilled GTC orders before first tick. Stale orders can silently block all exits.
- **Dual time horizon**: most positions short/fast, but some warrant a longer hold (DVN held ~20hrs Jul 22-23 before its MACDh exit — already in scope). Per-position qualitative call, not a fixed rule yet.

## Risk Management

- Per-ticker size cap, diversification enforced (`risk.max_position_pct`).
- Stops non-negotiable once triggered.
- No single position sized to wipe out several small wins.
- Max positions/sector + daily order-count audit (`risk_guards`).

## Growth Trajectory

Universe/sizing constraints are a starting point, not a ceiling. As real track record accumulates (win rate, portfolio growth — my own call, reassessed at nightly Evolve, never a hardcoded milestone), sanctioned to widen toward larger-cap names and larger sizes. Same "harden what's proven" bar as any other evolution.

## What I'm Learning

- Daily order-count guardrail (`risk_guards.order_count_audit_threshold_daily`, currently 30) is a self-imposed rogue-loop backstop, not an Alpaca limit — Alpaca's actual constraint is a 200 req/min API rate cap.
- **Parallel tick collision — 2nd occurrence (Jul 27)**: KRC got 2 shares instead of 1 due to parallel tick collision at 10:20/10:25, same bug as IP Jul 24. The order-idempotency guard (documented Jul 24 in MEMORY.md) was never implemented. Two occurrences in 3 sessions — this is no longer an edge case. Before submitting a buy order, the executor MUST check for existing pending/active orders on that symbol.
- One outsized loser wipes out several small winners — sizing > pick quality.
- High conviction ≠ high accuracy — keep calibrating.
- Stale pipeline is worse than no pipeline — fall back to price action honestly.
- Pre-market quotes are dealer indications, not price discovery — don't adjust conviction on them.
- Promotion bar is split-window Sharpe (both halves positive independently), not raw aggregate return — a good full-window number can hide a strategy that's actually broken in the more recent half.
- Watchlist must be fed — 4 days with no new names means the pipeline's starved; 2-3/session minimum. Mechanically enforced end to end, not just a reminder: `scripts/discovery_scan.py` generates candidates, `scripts/merge_discoveries.py` feeds them into the watchlist every tick, and `scripts/discovery_urgency_check.py` forces an immediate scan — checked every tick — whenever the candidate pipeline goes empty, plus on a 45-min cron whenever cash sits mostly idle with a thin pipeline.
- Low-confidence/CHOPPY: don't trust MACD divergence without price confirmation — it's a trap.
- Near-zero MACDh oscillation (±0.003 range on a flat price, declining-but-improving bars, MACD line holding) is noise, not a signal — don't exit. Real flips are 4-5 bar declining trends with price confirmation.
- Pre-session account audit: audit against journal records symbol-by-symbol before first tick.
- Strategy changes must be verified end-to-end: after revising `strategy.md`, explicitly check `params.json`, `executor.py`, and `tick_prompt.md` for stale rules. A revision that only touches this file is incomplete.


## Evolution Process
- Not limited to this file — evolve when something real is learned (nightly Step 3).
- Version `stonks.strat:v{major}.{minor}`; experimental `x-{name}` (5 trades, promote or revert).
- "Nothing changed" is valid.
- Run `skills/rule-mechanization-audit.md` every cycle — surfaces which prose rules here are repeatedly violated (mechanize candidates) vs. no longer relevant (removal candidates).
- No Version History section here — git already tracks it. Write the real rationale into the commit message when you bump the version (`git log strategy.md` to read it back); keep this file itself trim.
