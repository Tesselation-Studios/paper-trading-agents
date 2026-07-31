# Skill: Social Sentiment (Reddit / StockTwits / Bluesky)

`get_social(source)` — real MCP tool (`paper-trading-rebuild/src/data_bus.py`). `source`: `"reddit"`, `"stocktwits"`, `"bluesky"`, or `"all"` (default). Returns raw posts per source plus a basic aggregate sentiment score — cache-first, falls back to a live fetch (Reddit has its own three-level fallback: RSS → search → browser scrape) with a bounded timeout.

**When to call it**: at entry-decision time (`tick_prompt.md` step 8), for a ticker you're actually about to buy — not every tick, not the whole watchlist. This is retail-conviction/hype context, not a trigger signal.

**How to weigh it — this is a judgment call, not a number to trust blindly**: the returned `sentiment_score` is mechanical (post-count-weighted, no understanding of sarcasm, pump-and-dump chatter, or a single loud poster dominating a thin thread). Read the actual `posts` when the score looks meaningful — a genuinely building thesis across many distinct posters reads very differently from three hype posts in an illiquid name. Retail chatter agreeing with your other evidence (fundamentals, congress trades, wiki narrative) is real corroboration; retail chatter as the *only* signal on an otherwise thin case is exactly the kind of thing that's burned Stan before (see `strategy.md`'s "What I'm Learning" — high conviction ≠ high accuracy). Fold it into the rationale like any other gestalt signal (`skills/self-improving-agent.md`), don't cite the raw score alone.

**Skip on error or empty results** — like every other data-bus call here, never block a trade on it. Reddit specifically can return zero posts if all three of its fallback levels miss (rate-limited, blocked, or genuinely no chatter) — that's a real "no signal," not a broken call.
