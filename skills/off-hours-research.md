# Skill: Off-Hours Research Routine

Runs daily 20:00 ET via the `stonks-off-hours` cron. Market closed — no trading, don't call `scripts/executor.py`. Read `strategies/watchlist.md` + `positions/*.md` for current tickers first, pass them explicitly to the scripts below (no stale defaults).

| Step | Command | Output |
|---|---|---|
| 1. News + sentiment | `python3 scripts/news_collector.py <tickers>` | Fresh RSS → `public.news_cache` Postgres (additive, `ON CONFLICT DO NOTHING`). Sentiment over last 24h. Note real catalysts, not noise. |
| 2. Exercise wired signals | `get_macro` (once), `get_fundamentals`/`get_flow`/`get_insiders`/`get_risk`/`get_technical_scan` (per ticker) | Zero-risk since the market's closed — this is where those tools actually get practiced. Log real-vs-errored per tool; `get_risk`/`get_technical_scan` may still error (LoneStarOracle down, separate known gap). See `skills/self-improving-agent.md`. |
| 3. Signal scorecard | `python3 scripts/signal_scorecard.py` | Real empirical hit rate per signal from `trading.training_examples`. Signals under 10 labeled examples show `insufficient_data`, not a fake precise number — that's expected while volume is low, not a bug. |
| 4. Replay check | `python3 scripts/replay_check.py <tickers>` | 200d backtest, v1.0 vs v1.1 vs v1.2 side by side — empirical check on whether hardened rules actually help. Caveat: RSI-band entry proxy, not full qualitative judgment; v1.2's sector veto/VIX-tiering/quality-gate aren't modeled (no sector/VIX/fundamentals data here). |
| 5. Brief log | Write 5-10 lines to `off_hours/YYYY-MM-DD.md` | Notable news, which signals worked vs errored, scorecard status changes, and replay numbers + whether they still support the current strategy version. Do NOT edit `strategy.md`/`params.json` here — that's the nightly job's call, with fuller context. |
