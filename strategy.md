# Stonks — Strategy stonks.strat:v1.2.1

## Philosophy

Trade small and wide, not concentrated — volume of decisions and honest feedback over a hero trade. Qualitative judgment that keeps proving out gets promoted into `params.json`/code.

## Current Approach

- **Universe**: $1–$50, small/mid-cap, reasonable liquidity. Starting constraint, not permanent — see Growth Trajectory.
- **Discovery**: `strategies/watchlist.md`, grow/shrink by notice/idle-ticks (`params.json: watchlist.idle_ticks_before_drop`). Manual/qualitative, no screener/ML yet.
- **Sizing**: small per-position, many concurrent, diversification over conviction (`params.json: risk`).
- **Pre-entry gates**: debt/equity + FCF yield sanity check (`quality_gate`); sector ETF veto if sector's trending down (`entry_rules.sector_etfs_for_veto`); no entries within `entry_rules.earnings_blackout_days` of earnings.
- **Entry signals** (qualitative, hardens into code once patterns repeat):
  - Momentum: rising RSI + volume + catalyst
  - Oversold bounce, gap plays (>3% pre-market + volume), sector heat, sentiment divergence
  - Triple confirmation sizing (`entry_rules.triple_confirmation_weights`): RSI/MACD/MA trend, 2-of-3 = half-size, 3-of-3+catalyst+sector = full size
- **Exit**: stop/target from `params.json`, or thesis breaks. Never chase a peaked pump.
- **Trim at target** (`trim.profit_target_trim_pct`) instead of binary exit.
- **Hard exit — MACDh flip**: positive→negative on a held position = sell immediately, no deliberation.
- **Hard exit — RSI exhaustion**: RSI > `exit_rules.rsi_exhaustion_hard_exit` (75) = sell, not negotiated by MACD.
- **Time-stop**: `risk_guards.max_holding_days` backstop.
- **Regime gate**: no entries in CHOPPY (<0.5 conf) or FEAR (F&G<30) — manage existing, no fresh capital. Sizing also scales continuously with vol regime (`regime_sizing` VIX tiers).
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
- MACDh flip = most reliable near-term exit. Codified v1.1.
- Regime-gating entries works — cash preserved, win rate up. Codified v1.1.
- Watchlist must be fed — 4 days with no new names means the pipeline's starved; 2-3/session minimum.
- Low-confidence/CHOPPY: don't trust MACD divergence without price confirmation — it's a trap (Aldridge).
- Watchlist discovery is decoupled from entry gating — run daily small-cap scan regardless of regime. Add 2+ new names per session whether or not entries are open. Codified v1.2.1.
- Pre-session GTC order audit — clear all stale/unfilled GTC orders before first tick. Stale orders can silently block all exits (Jul 21 near-miss: 11 unfilled orders). Codified v1.2.1.
- v1.2 triple-confirmation entry may be too permissive — 170 trades vs 99 for v1.0 over same 200d/22-ticker sample. Two nights of backtest data consistent. Monitoring; promotion treated as unsettled.

## Regime Playbook
- **TRENDING_UP**: full size. **CHOPPY**: half size, wider stops, confirmed catalysts only. **TRENDING_DOWN**: defensive, no new buys.

## Evolution Process
- Not limited to this file — evolve when something real is learned (nightly Step 3).
- Version `stonks.strat:v{major}.{minor}`; experimental `x-{name}` (5 trades, promote or revert).
- "Nothing changed" is valid.

## Version History

- `v1.2.1` (2026-07-21) — Hardened two patterns from 7-entry journal synthesis: (1) decoupled watchlist discovery from entry gating — scan and add candidates daily regardless of regime; (2) pre-session GTC order audit — clear stale orders before first tick. Flagged v1.2 triple-confirmation entry rules as under monitoring — two nights of corrected backtest data shows v1.2 underperforming v1.0 on entries (170 vs 99 trades, -3.05% vs -0.08% return).
- `v1.2` (2026-07-18) — Pulled pruned knowledge from retired Kairos (RSI exhaustion exit, triple-confirmation sizing, sector veto, earnings blackout, VIX-tiered sizing, sector/order-count guards, time-stop) + Aldridge (quality gate, trim discipline, CHOPPY lesson). Added Growth Trajectory + dual time-horizon. Excluded: Kairos's MACD-divergence-as-accumulation read (conflicts with v1.1's hard MACDh exit, already validated by replay_check.py); Aldridge's micro-cap sub-schema + Kairos's sub-$10/volume floors (conflict with small-cap mandate).
- `v1.1` (2026-07-18) — Hardened MACDh-flip exit + CHOPPY/FEAR entry gate from 5 days of journal patterns. Added watchlist-feeding discipline.
- `v1.0` (2026-07-17) — Consolidation MVP: reset from concentrated conviction plays to small-cap/wide/high-rep, onto Aldridge's tick-loop architecture. ML/fundamentals/news-aggregation deferred.
