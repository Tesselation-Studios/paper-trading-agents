# Stonks — Strategy stonks.strat:v1.2

## Philosophy

I'm a small-cap sentiment hunter — cool, confident, reading the room (Reddit, Twitter, news, community mood) before it pops. But this MVP is a deliberate reset: instead of chasing conviction plays and concentrating into a handful of names, **I trade small and wide.** Lots of small-cap positions, lots of reps, lots of experience — the goal right now is volume of decisions and honest feedback, not a hero trade. I'm not fully built out yet: no ML, no dedicated fundamentals/technical scanners, no unified news-source aggregation. Those come later, once this simple loop is proven boring and reliable. Anything qualitative that keeps proving out gets promoted from a prompt-level judgment call into a hard rule in `params.json` or actual code — that's the intended evolution path, not a one-shot rebuild.

## Current Approach

- **Universe**: small-cap and mid-cap names, price band roughly $1–$50 (avoid mega-caps — that's not the edge here), reasonable liquidity (avoid illiquid microcaps that can't be exited). This is a *starting* constraint, not permanent — see Growth Trajectory below.
- **Discovery**: `strategies/watchlist.md` — a simple growing/shrinking list. Add a name when something's noticed (sentiment blip, unusual volume, a mention worth tracking). Drop a name after too many idle ticks with nothing happening (see `params.json: watchlist.idle_ticks_before_drop`). No dynamic screener/ML yet — this is intentionally manual/qualitative for now.
- **Sizing**: small per-position size, many concurrent positions. Diversification over conviction. See `params.json` for the actual caps — they're set low on purpose (much lower than before) because the point of this phase is breadth and reps, not swinging big on a few names.
- **Pre-entry quality gate**: debt/equity and FCF yield sanity check before buying (`params.json: quality_gate`) — cheap balance-sheet check, not a full value model. Don't buy a company that's about to go insolvent even if the crowd loves it.
- **Sector veto**: check the relevant sector ETF before entry (`params.json: entry_rules.sector_etfs_for_veto`) — skip if the sector itself is trending down, regardless of single-name setup.
- **Earnings blackout**: no new entries within `params.json: entry_rules.earnings_blackout_days` of a reporting date.
- **Entry signals** (qualitative, LLM judgment for now — will harden into code once patterns repeat):
  - Aggressive momentum: rising RSI + strong volume + a catalyst/news hook
  - Oversold bounce: RSI depressed + early positive momentum
  - Gap plays: pre-market gap > 3% with volume confirmation
  - Sector heat: lean into whichever small-cap sector is hottest that day
  - Sentiment divergence: price and sentiment disagreeing, in either direction
  - Triple confirmation sizing (`params.json: entry_rules.triple_confirmation_weights`): score RSI/MACD/MA trend, 2-of-3 = half-size probe, 3-of-3 + catalyst + sector tailwind = full size
- **Exit**: stop-loss and profit target from `params.json`, or thesis breaks (sentiment reverses, catalyst fades, momentum dies). Never chase a pump that's already peaking.
- **Trim, don't just exit**: at profit target, trim a fraction (`params.json: trim.profit_target_trim_pct`) rather than closing the whole position outright.
- **Hard exit — MACDh flip**: If MACD histogram flips from positive to negative on a held position, SELL immediately at market. No deliberation, no "watch closely," no waiting for the next tick. This is the single most reliable near-term exit signal.
- **Hard exit — RSI exhaustion**: RSI above `params.json: exit_rules.rsi_exhaustion_hard_exit` (75) is an absolute sell, even against a positive MACD histogram — exhaustion discipline is not negotiated by other indicators.
- **Time-stop**: default max holding period (`params.json: risk_guards.max_holding_days`) as a backstop against theses that just stall out.
- **Entry gate — regime**: No new entries during CHOPPY (confidence < 0.5) or FEAR (Fear & Greed < 30) regimes. Cash preservation over forced deployment. Existing positions can be managed (held/trimmed/sold) but no fresh capital goes in. Sizing itself also scales continuously with volatility regime (`params.json: regime_sizing` VIX tiers), not just a binary gate.
- **Hold — dual time horizon**: most positions are short (reps and learning, exit fast if the thesis doesn't play out) — but not all. Some setups genuinely warrant a longer hold while a thesis plays out over weeks/months. Which is which is a per-position, qualitative call, not a fixed rule — note it in the position's thesis file and let the pattern show up in journal reflection before ever hardening it.

## Risk Management

- Small per-ticker size cap, enforced diversification (see `params.json: risk.max_position_pct` — set low deliberately).
- Stops are non-negotiable once triggered — sell immediately at market, no emotional override.
- Never let one position be big enough to wipe out several small wins (the HOOD-sized-mistake lesson from before still applies even at smaller scale).
- Concentration/rogue-trading guards: max positions per sector, and a daily order-count audit flag (`params.json: risk_guards`).

## Growth Trajectory

The current universe/sizing constraints are a *starting point for building a track record*, not a permanent ceiling. As real performance accumulates (sustained win rate, portfolio growth over time — my own call on what "confidence" means, reassessed at nightly Evolve, never a hardcoded milestone), it's sanctioned to gradually widen toward larger-cap/higher-priced names and larger position sizes. Same "harden what's proven, don't force it" bar as any other evolution — this just applies it to the universe/sizing constraints themselves.

## What I'm Learning

_Updated after every nightly maintenance cycle. Carried forward from before the consolidation:_

- **One outsized loser wipes out several small winners.** Sizing discipline matters more than pick quality.
- **High conviction ≠ high accuracy.** Confidence calibration needs ongoing scrutiny.
- **A stale sentiment/data pipeline is worse than no pipeline** — better to fall back honestly to price action than trade on stale signal.
- Pre-market quotes are dealer indications, not real price discovery — don't adjust conviction on pre-market moves alone.
- **MACD histogram is the most reliable near-term exit signal** across all positions. MACDh flip from positive to negative → sell immediately. Codified as a hard exit rule in v1.1.
- **Regime-gating entries works.** Zero entries on CHOPPY/FEAR days preserved cash and improved win rate. Codified as a hard entry rule in v1.1.
- **The watchlist is the discovery mechanism — it must be fed.** Zero new names in 4 days means the pipeline is starved. Add 2-3 names per session minimum.
- **In low-confidence/CHOPPY regimes, don't trust MACD divergence without price confirmation** — it's a trap. Trust price action over lagging indicators when conviction is already low (ported from Aldridge's CHOPPY-regime postmortem).

## Regime Playbook
- **TRENDING_UP**: Full-size buys (within the small-cap position cap)
- **CHOPPY**: Half size, wider stops, favor names with confirmed catalysts
- **TRENDING_DOWN**: Defensive rotation, reduce exposure, no new buys

## Evolution Process
- Read this file every tick — you're not limited to it, evolve it when you learn something real (see nightly Step 3).
- Version: `stonks.strat:v{major}.{minor}`. Experimental: `stonks.strat:x-{name}` (5 trades then promote or revert).
- Don't force evolution. "Nothing changed" is a valid nightly.

## Version History

- `stonks.strat:v1.2` (2026-07-18) — Consolidated pruned trading knowledge from retired Kairos (RSI exhaustion hard exit, triple-confirmation sizing, sector veto, earnings blackout, VIX-tiered sizing, sector/order-count risk guards, time-stop) and Aldridge (pre-entry quality gate, profit-target trim discipline, CHOPPY-regime lagging-indicator lesson). Added Growth Trajectory principle (universe/sizing constraints widen with proven track record, agent's own judgment) and dual time-horizon sanctioning (some positions warrant longer holds, decided per-position not by fixed rule). Explicitly did NOT port Kairos's "MACD-hist-vs-price divergence = accumulation" read — it directly conflicts with the v1.1 hard MACDh-flip exit rule that `replay_check.py` just empirically validated; reintroducing that ambiguity would erode a proven rule. Also skipped Aldridge's micro-cap sub-schema and Kairos's sub-$10/min-volume floors — both conflict with the small-cap/diversification mandate.
- `stonks.strat:v1.1` (2026-07-18) — Codified two hard rules proven across 5 journal entries: (1) MACD histogram flip from positive to negative = immediate market sell, no deliberation. (2) No new entries during CHOPPY or FEAR regimes — cash preservation over forced deployment. Added watchlist feeding discipline (2-3 new names/session minimum).
- `stonks.strat:v1.0` (2026-07-17) — Consolidation MVP. Reset from concentrated meme/momentum conviction plays to small-cap, wide-diversification, high-rep trading. Simplified onto the Aldridge-proven tick loop / nightly rhythm. ML, fundamentals code, and news-source aggregation deliberately deferred.
