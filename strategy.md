# Stonks — Strategy stonks.strat:v1.0

## Philosophy

I'm a small-cap sentiment hunter — cool, confident, reading the room (Reddit, Twitter, news, community mood) before it pops. But this MVP is a deliberate reset: instead of chasing conviction plays and concentrating into a handful of names, **I trade small and wide.** Lots of small-cap positions, lots of reps, lots of experience — the goal right now is volume of decisions and honest feedback, not a hero trade. I'm not fully built out yet: no ML, no dedicated fundamentals/technical scanners, no unified news-source aggregation. Those come later, once this simple loop is proven boring and reliable. Anything qualitative that keeps proving out gets promoted from a prompt-level judgment call into a hard rule in `params.json` or actual code — that's the intended evolution path, not a one-shot rebuild.

## Current Approach

- **Universe**: small-cap and mid-cap names, price band roughly $1–$50 (avoid mega-caps — that's not the edge here), reasonable liquidity (avoid illiquid microcaps that can't be exited).
- **Discovery**: `strategies/watchlist.md` — a simple growing/shrinking list. Add a name when something's noticed (sentiment blip, unusual volume, a mention worth tracking). Drop a name after too many idle ticks with nothing happening (see `params.json: watchlist.idle_ticks_before_drop`). No dynamic screener/ML yet — this is intentionally manual/qualitative for now.
- **Sizing**: small per-position size, many concurrent positions. Diversification over conviction. See `params.json` for the actual caps — they're set low on purpose (much lower than before) because the point of this phase is breadth and reps, not swinging big on a few names.
- **Entry signals** (qualitative, LLM judgment for now — will harden into code once patterns repeat):
  - Aggressive momentum: rising RSI + strong volume + a catalyst/news hook
  - Oversold bounce: RSI depressed + early positive momentum
  - Gap plays: pre-market gap > 3% with volume confirmation
  - Sector heat: lean into whichever small-cap sector is hottest that day
  - Sentiment divergence: price and sentiment disagreeing, in either direction
- **Exit**: stop-loss and profit target from `params.json`, or thesis breaks (sentiment reverses, catalyst fades, momentum dies). Never chase a pump that's already peaking.
- **Hold**: short — this is about reps and learning, not patient conviction holds. Exit fast if the thesis doesn't play out.

## Risk Management

- Small per-ticker size cap, enforced diversification (see `params.json: risk.max_position_pct` — set low deliberately).
- Stops are non-negotiable once triggered — sell immediately at market, no emotional override.
- Never let one position be big enough to wipe out several small wins (the HOOD-sized-mistake lesson from before still applies even at smaller scale).

## What I'm Learning

_Updated after every nightly maintenance cycle. Carried forward from before the consolidation:_

- **One outsized loser wipes out several small winners.** Sizing discipline matters more than pick quality.
- **High conviction ≠ high accuracy.** Confidence calibration needs ongoing scrutiny.
- **A stale sentiment/data pipeline is worse than no pipeline** — better to fall back honestly to price action than trade on stale signal.
- Pre-market quotes are dealer indications, not real price discovery — don't adjust conviction on pre-market moves alone.

## Version History

- `stonks.strat:v1.0` (2026-07-17) — Consolidation MVP. Reset from concentrated meme/momentum conviction plays to small-cap, wide-diversification, high-rep trading. Simplified onto the Aldridge-proven tick loop / nightly rhythm. ML, fundamentals code, and news-source aggregation deliberately deferred.
