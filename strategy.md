# Stonks — Strategy stonks.strat:v1.20

## Philosophy

Trade small and wide, not concentrated — volume of decisions and honest feedback over a hero trade. Qualitative judgment that keeps proving out gets promoted into `params.json`/code. Promotion requires split-window Sharpe positive in both halves independently, not aggregate return.

**Doing nothing is a cost, not a safe default.** A quiet book is a failure mode. No action needed on current positions is the cue to go find a new one, not to coast.

## Current Approach

- **Universe**: $1–$500, reasonable liquidity; bankroll-scaled max price grows further — see Growth Trajectory.
- **Discovery**: `state/trader.db`'s watchlist_candidates table, grows/shrinks by notice/idle-ticks (`params.json: watchlist.idle_ticks_before_drop`). Decoupled from entry gating — `scripts/merge_discoveries.py` runs every tick regardless of regime.
- **Sizing**: small per-position, many concurrent, diversification over conviction (`params.json: risk`). 1-3 shares is a legitimate position. No hard cap on position count — cash, `max_position_pct`, and the bankroll ceiling are the real limiters.
- **Entry is a judgment call, not a formula**: RSI, volume, momentum, and regime are signals to weigh, not a checklist. Reason over the whole gestalt for a name — world/sector narrative, congressional trades, fundamentals, cross-sectional momentum rank, technicals — and state real conviction in the rationale. Two signals pointing the same direction is stronger evidence than either alone; say so. `conviction_floor` in `params.json` is a sanity check, not a threshold to clear. No catalyst requirement, no triple-confirmation, no sector-ETF veto, no VIX-tiered sizing.
- **Catalyst-led entry liquidity gate**: catalyst-led entries on sub-$500M market-cap names require a pre-entry daily dollar volume floor — if < $50K/day average dollar volume, skip regardless of catalyst quality. Mechanically enforced in `executor.py`'s `gate_catalyst_liquidity` (`risk_guards.catalyst_liquidity_gate`) — pass `--market-cap`/`--avg-dollar-volume` on the BUY call for a sub-$500M catalyst-led candidate; the gate fails open (doesn't block) without that data.
- **Two capital buckets** (`params.json: risk.long_play` / `risk.conviction_play`): most of the book is the wide/fast opportunistic sweep above. A smaller conviction bucket is for well-known/liquid names with an actual researched thesis — larger size, wider trailing stop (never exempt from it, or from the hard stop), held until the thesis plays out or breaks rather than a mechanical exit. Long plays are a separate, smaller, evidence-gated short-horizon exception (explicit prediction + resolution date). A subset of the conviction bucket may hold broad-market index ETFs as a cash-deployment anchor — see Index-anchor conviction positions.
- **Index-anchor conviction positions**: a small number of the 5 conviction-play slots may be used for this instead of a single-name thesis.
  - **Eligible instruments**: SPY, QQQ, DIA, IWM only — broad, mega-cap-liquid, non-leveraged, non-inverse, non-sector/thematic index ETFs.
  - **Thesis condition for holding**: no prolonged-downturn regime signal — `get_market_regime` not reading a sustained bearish/distribution regime across multiple sessions, and the ETF's own technicals (MACDh/RSI) not in a multi-session declining trend. Score with `record_decision.py reconcile`, built from `regime`/`macro`/cross-index `technical` signal keys instead of the usual single-company ones; can clear `agreement: true` / `signal_count >= 3` / `combined_confidence >= 0.60` on that basis. State the regime read explicitly in `--thesis-claim`/`--prediction-reason`.
  - **Exit / downsize — two independent triggers**:
    1. **Thesis break** (full or partial exit): `get_market_regime` shifts to a genuine sustained bearish/distribution read (more than one session), or the ETF's own MACDh/RSI turns and stays down across multiple ticks. Route through Daily thesis re-confirmation, `--verdict broken`, `--close-reason 'thesis broken: <condition>'`.
    2. **Reallocation sell-down** (partial, not a thesis break): selling part of an index-anchor position to free cash for a new, separately-qualifying individual-stock opportunity is valid and routine — the anchor exists to be spent down into better ideas. Log as `--close-reason 'reallocation: funding <ticker> entry'`.
  - **Slot budget**: earmark 2 of the 5 conviction slots for index-anchors, never more than 3 — leave at least 2 free for genuine individual-stock conviction picks.
  - **Sizing exception, current policy (2026-08-10, Raf's direction)**: an index-anchor position is explicitly exempt from `risk.conviction_play.position_size_pct` on the upside — if it organically sits above that cap, that's not a breach and `check_stops()`'s oversized-position trim does not apply to it. This is deliberately **not** an instruction to actively buy more into it, though — at least for now, don't add to an index-anchor position beyond its current size. When a new individual-stock opportunity needs cash and the anchor is available, sell it down (the existing Reallocation sell-down trigger above) rather than deploying fresh capital alongside it. Revisit this policy once the tree's `conviction_play_anchor_v1`/individual-stock conviction picks have enough track record to judge whether growing the anchor further is still the right lever.
  - **Exempt from the per-tick market-context exit layer**, same as any conviction play — no fast intraday SPY-fade trim. Only the daily thesis re-confirmation, the always-on hard stop-loss, and the wider trailing stop apply.
- **Conviction-play eligibility anchor**: reach for `--play-type conviction` when `record_decision.py reconcile`'s output shows `agreement: true` AND `signal_count >= 3` directional signals AND `combined_confidence >= 0.60`. Numeric anchor for a judgment call, not an auto-trigger — Stan may still choose conviction sizing outside it, or decline inside it, stated explicitly in `--rationale` either way. Every conviction/long play requires `--thesis-claim` (falsifiable directional claim) and `--thesis-invalidation` (specific, checkable condition that would prove it wrong).
- **Exit**: Hard stop-loss (`risk.stop_loss_pct`, -6%) is a non-negotiable full exit. Volatility-scaled trailing stop ratchets up from peak price (TRAIL_K=40, clamped 7-18% based on 20d realized vol). Profit-target (`risk.profit_target_pct`, 10%) is a **guide, not an automatic trigger** — near it, use judgment. Also exit on thesis breaks. MACDh-flip is not a mandatory exit. Never chase a peaked pump — on a fresh MACDh flip, if RSI is already extended (>65 bullish / <35 bearish) and price has already run meaningfully (>1%) in the flip direction, treat that as a peaked-pump pattern: require an additional independent confirming signal before entering, size down, or wait for a pullback. Graduated, not a hard cutoff. All stops are mechanically enforced in `executor.py`'s guardrail gates.
- **Market-context exit discipline** (higher-priority judgment layer, on top of the position's own stop/target): if an open position is up meaningfully (roughly >2% intraday) and SPY's own trend turns — RSI drops materially (>8pts) or MACDh flips/declines meaningfully — take profits (trim or close) rather than holding for the full target on the position's own signals alone.
  **Exempt**: an open `conviction_play`, or an active (unexpired) `long_play` — evaluated against their own recorded thesis on a daily cadence instead. The hard stop-loss and the position's own trailing stop remain fully active regardless of play_type; this exemption removes only the market-context judgment layer.
- **Daily thesis re-confirmation**: once per ET trading day per open conviction/long play, gated on `positions.thesis_last_checked_at` (first tick after 09:30), re-run `reconcile` with fresh `--features` and diff against `positions.thesis_entry_signals`; check current reality against `thesis_invalidation`. Write the verdict with `trader_write.py position-update-thesis --verdict intact|weakening|broken --note '...'`. `intact` → no action. `weakening` → flag in `active.md`, no forced exit (two consecutive `weakening`s is a reasonable prompt to reassess). `broken` → a real SELL, `--close-reason 'thesis broken: <condition>'`.
- **Bootstrap-phase profit-taking bias** (`params.json: bootstrap_phase`): while bankroll's ceiling is below `ceiling_threshold`, tilt the profit-target judgment toward taking a clearly-positive win (past `quick_exit_min_return_pct`) rather than holding for the full target. As ceiling grows past the threshold, revert to the let-winners-run judgment above. Never sell at a loss to force a "win"; thesis breaks/stops unaffected.
- **Pre-session GTC order audit**: clear all stale/unfilled GTC orders before first tick.
- **Dual time horizon**: most positions short/fast, some warrant a longer hold. Per-position qualitative call.

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
- Low-confidence/CHOPPY: don't trust MACD divergence without price confirmation — it's a trap.
- Near-zero MACDh oscillation (±0.003 range on a flat price) is noise, not a signal — don't exit. Real flips are 4-5 bar declining trends with price confirmation.
- Pre-session account audit: audit against journal records symbol-by-symbol before first tick.
- Strategy changes must be verified end-to-end: after revising `strategy.md`, check `params.json`, `executor.py`, and `tick_prompt.md` for stale rules.
- v1.20 (2026-08-05): scale-into-winners (v1.7 addition) was actively destructive — degraded v1.1's Sharpe 4.618 → 2.099, first-half near-flat. Reverted to v1.1 core. Stop/target tightened (-10%,12%) → (-6%,10%) per split-window sweep: Sharpe 3.982 vs 3.148, higher-rep pattern (198 vs 124 trades).

## Evolution Process

- Not limited to this file — evolve when something real is learned (nightly Step 3).
- Version `stonks.strat:v{major}.{minor}`; experimental `x-{name}` (5 trades, promote or revert).
- "Nothing changed" is valid.
- Run `skills/rule-mechanization-audit.md` every cycle — surfaces which prose rules here are repeatedly violated (mechanize candidates) vs. no longer relevant (removal candidates).
- No Version History section here — git tracks it. Write rationale into the commit message when bumping the version (`git log strategy.md` to read it back).
