# Stonks — Strategy stonks.strat:v1.4

## Philosophy

Trade small and wide, not concentrated — volume of decisions and honest feedback over a hero trade. Qualitative judgment that keeps proving out gets promoted into `params.json`/code. Promotion requires the backtest to actually hold up — split-window Sharpe (positive in BOTH halves of the window independently), not a single aggregate-return number. Full rationale for any version change lives in the git commit that made it, not here — `git log strategy.md`.

## Current Approach

- **Universe**: $1–$50, small/mid-cap, reasonable liquidity. Starting constraint, not permanent — see Growth Trajectory.
- **Discovery**: `strategies/watchlist.md`, grow/shrink by notice/idle-ticks (`params.json: watchlist.idle_ticks_before_drop`). Decoupled from entry gating — `scripts/merge_discoveries.py` runs every tick regardless of regime, feeds probe-discovery output into the watchlist mechanically.
- **Sizing**: small per-position, many concurrent, diversification over conviction (`params.json: risk`).
- **Entry signal**: rising RSI in the 45-65 band + volume + a real catalyst, skipped when 20-day realized volatility is elevated (CHOPPY/high-vol regime). No triple-confirmation, no sector-ETF veto, no VIX-tiered sizing.
- **Exit**: MACD histogram flip (positive→negative) triggers an immediate exit. Stop-loss (`risk.stop_loss_pct`) is a hard, non-negotiable full exit. Profit-target (`risk.profit_target_pct`) is a **guide, not an automatic trigger** (`risk.profit_target_is_guide`) — near it, use judgment: real momentum can justify holding past it, a stalling move should exit at or before it. Also exit on thesis breaks. Never chase a peaked pump. Hard stop and trailing stop are mechanically enforced in `executor.py`'s guardrail gates regardless of strategy version.
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
- Promotion bar is split-window Sharpe (both halves positive independently), not raw aggregate return — a good full-window number can hide a strategy that's actually broken in the more recent half.
- Watchlist must be fed — 4 days with no new names means the pipeline's starved; 2-3/session minimum. Mechanically enforced (`scripts/merge_discoveries.py`), not just a reminder.
- Low-confidence/CHOPPY: don't trust MACD divergence without price confirmation — it's a trap.
- Near-zero MACDh oscillation is noise, not a signal: when histogram crosses near zero (±0.003 range) on a $1-$50 stock with flat price, declining-but-improving bars, and MACD line holding structurally above/below zero — don't exit. Verified 3+ times under live fire (Jul 23: DVN, WSC, F). Real flips are unmistakable — 4-5 bar declining trends with price confirmation.
- Pre-session account audit — shared credentials mean your Alpaca account may contain positions you don't recognize. Audit against journal records symbol-by-symbol before first tick.
- Strategy changes must be verified end-to-end: after revising `strategy.md`, explicitly check `params.json`, `executor.py`, and `tick_prompt.md` for stale rules. A revision that only touches this file is incomplete.
- Trailing stop win rate: 4W/8L (33%) across Jul 21-23 exits. Still holding at exactly the 33% monitoring threshold — 12 trail-stop exits now, need 8 more for the 20-exit review trigger. GME/OPEN added Jul 23 (both -5.0%/-5.1%).

## Evolution Process
- Not limited to this file — evolve when something real is learned (nightly Step 3).
- Version `stonks.strat:v{major}.{minor}`; experimental `x-{name}` (5 trades, promote or revert).
- "Nothing changed" is valid.
- Run `skills/rule-mechanization-audit.md` every cycle — surfaces which prose rules here are repeatedly violated (mechanize candidates) vs. no longer relevant (removal candidates).
- No Version History section here — git already tracks it. Write the real rationale into the commit message when you bump the version (`git log strategy.md` to read it back); keep this file itself trim.
