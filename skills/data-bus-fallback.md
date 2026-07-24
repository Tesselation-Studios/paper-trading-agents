# Skill: Data Bus (best-effort context only, never load-bearing)

```bash
curl -s http://localhost:5000/quotes?symbols=SPY,AAPL,SOFI,PLTR | python3 -m json.tool
curl -s http://localhost:5000/momentum | python3 -m json.tool
curl -s http://localhost:5000/fear_greed | python3 -m json.tool
```

Sentiment moved off this endpoint 2026-07-23 — see `skills/sentiment-cache.md` (`state/sentiment_cache.json`), not `/sentiment` here. That endpoint's per-ticker keys were never actually populated by anything.

## MCP tools (prefer these over raw curl where both exist)

Regime, risk, macro, technical scan, options flow, insider filings, and fundamentals are real MCP tools (`get_market_regime`, `get_risk`, `get_macro`, `get_technical_scan`, `get_flow`, `get_insiders`, `get_fundamentals` — see `skills/fundamentals.md`), not just REST curl targets — call the tool directly rather than curling `/macro` or `/risk`. `get_flow`/`get_insiders`/`get_technical_scan`/`get_fundamentals` are entry-time checks (see `tick_prompt.md` step 8), not every-tick calls.

LoneStarOracle (real, connected 2026-07-24) backs several of these. Its free tier only covers `options_flow`/`insider_trading`/most of `macro_indicators` — `portfolio_risk` (`get_risk`) and `multi_timeframe_scan` (`get_technical_scan`) are paywalled (x402/USDC) and report a clean "unavailable" by design. Not a bug, not worth re-checking each tick — Raf's call: free-tier only for now.

If any of these are down or return stale timestamps, note it in `active.md` and proceed without them. This service has been the recurring cause of stale-data outages across prior builds (paper-trading-teams, paper-trading-rebuild postmortems) — never let a tick stall waiting on it. `scripts/executor.py` (direct Alpaca) is always the source of truth for cash/positions/P&L, never this.
