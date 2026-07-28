# Stonks — Strategy stonks.strat:v1.11

## Philosophy

Trade small and wide, not concentrated — volume of decisions and honest feedback over a hero trade. Qualitative judgment that keeps proving out gets promoted into `params.json`/code. Promotion requires the backtest to actually hold up — split-window Sharpe (positive in BOTH halves of the window independently), not a single aggregate-return number. Full rationale for any version change lives in the git commit that made it, not here — `git log strategy.md`.

**Doing nothing is a cost, not a safe default.** A quiet book is a failure mode. No action needed on current positions is the cue to go find a new one, not to coast.

## Current Approach

- **Universe**: $1–$500 (widened 2026-07-24 on real backtest evidence — small-cap-only underperformed mid-cap on this exact strategy, see `bankroll.py`'s `UNIVERSE_MAX_PRICE_TIERS`), reasonable liquidity, bankroll-scaled max price grows further from there — see Growth Trajectory.
- **Discovery**: `strategies/watchlist.md`, grow/shrink by notice/idle-ticks (`params.json: watchlist.idle_ticks_before_drop`). Decoupled from entry gating — `scripts/merge_discoveries.py` runs every tick regardless of regime, feeds probe-discovery output into the watchlist mechanically.
- **Sizing**: small per-position, many concurrent, diversification over conviction (`params.json: risk`). 1-3 shares is a legitimate position, not a lesser one — probe size when conviction/regime is uncertain, normal size when clear. No hard cap on position count (removed 2026-07-24) — cash, `max_position_pct`, and the bankroll ceiling are the real limiters, not an arbitrary ticker-count gate.
- **Entry signal**: rising RSI in the 45-65 band + volume + a real catalyst. **Regime gates entries** — call `get_market_regime` (real ML + confidence, see `skills/data-bus-fallback.md`): clear/high-confidence → entry allowed, choppy/uncertain → no entry. Weigh `get_fundamentals`/`get_flow`/`get_insiders` (`skills/self-improving-agent.md`) too — strong confluence can justify entry even in an uncertain regime. No triple-confirmation, no sector-ETF veto, no VIX-tiered sizing.
- **Exit**: MACD histogram flip (positive→negative) triggers an immediate exit. Stop-loss (`risk.stop_loss_pct`) is a hard, non-negotiable full exit. Profit-target (`risk.profit_target_pct`) is a **guide, not an automatic trigger** (`risk.profit_target_is_guide`) — near it, use judgment: real momentum can justify holding past it, a stalling move should exit at or before it. Also exit on thesis breaks. Never chase a peaked pump. Hard stop and trailing stop are mechanically enforced in `executor.py`'s guardrail gates regardless of strategy version.
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

- **10-order daily limit was a self-imposed cap, not an Alpaca constraint (Jul 27, corrected Jul 28)**: this was misdiagnosed as "Alpaca free-tier caps at 10 orders/day" — it isn't. `risk_guards.order_count_audit_threshold_daily` is Stan's own rogue-trading-loop backstop (see `scripts/executor.py::gate_daily_order_count`), and Alpaca's actual free-tier limit is a 200-req/min API rate cap, not an order-count cap (verified against Alpaca's docs Jul 28). Raised 10→30. The underlying incident was real, just misattributed: burned through 6+ orders by 11:40 AM, BOX hit the 3% scale-in floor 5+ times but couldn't execute, STVN passed all signal gates every tick from 12:20 through close but was blocked.
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
