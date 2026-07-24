# Skill: Fundamentals

`get_fundamentals(symbol)` — P/E, EPS, dividend yield, market cap, ROE, analyst target. Real MCP tool (`paper-trading-rebuild/src/data_bus.py`), tiered fallback: cache → SQLite → Alpha Vantage (`skill_combo_fetch`) → SQLite → yfinance web.

**When to call it**: at entry-decision time (`tick_prompt.md` step 8), for a ticker you're actually about to buy — not every tick, not the whole watchlist. This is valuation context, not a trigger signal; fold it into conviction alongside technical/sentiment, don't gate a trade on it alone.

**How to weigh it**: no hard thresholds — this is a small-cap momentum strategy, not a value strategy, so a high P/E alone isn't disqualifying. Use it as a sanity check: a name with deeply negative EPS and no analyst coverage is a different risk profile than one with a reasonable P/E and a target price above current — note that distinction in the trade rationale.

**Known gap (2026-07-23)**: currently returns `"error": "no data available"` for every symbol, including mega-caps — not a bug in the tool, the underlying `skill_combo_fetch` module isn't installed anywhere in the engine repo's venv (`ModuleNotFoundError`), and the yfinance fallback is separately blocked from this homelab's network (same issue `scripts/backfill_bars_alpaca.py` was built to route around for bars). Confirmed via direct testing, independent of any recent code change. Treat empty fundamentals as expected until one of those two paths is fixed — skip on error like every other data-bus call here, never block a trade on it.
