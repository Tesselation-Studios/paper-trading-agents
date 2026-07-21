# Skill: Off-Hours Research Routine

Runs daily 20:00 ET via the `stonks-off-hours` cron. Market closed — no trading, don't call `scripts/executor.py`. Read `strategies/watchlist.md` + `positions/*.md` for current tickers first, pass them explicitly to both scripts below (no stale defaults).

| Step | Command | Output |
|---|---|---|
| 1. News + sentiment | `python3 scripts/news_collector.py <tickers>` | Fresh RSS → `public.news_cache` Postgres (additive, `ON CONFLICT DO NOTHING`). Sentiment over last 24h. Note real catalysts, not noise. |
| 2. Replay check | `python3 scripts/replay_check.py <tickers>` | 200d backtest, v1.0 vs v1.1 vs v1.2 side by side — empirical check on whether hardened rules actually help. Caveat: RSI-band entry proxy, not full qualitative judgment; v1.2's sector veto/VIX-tiering/quality-gate aren't modeled (no sector/VIX/fundamentals data here). |
| 3. Brief log | Write 5-10 lines to `off_hours/YYYY-MM-DD.md` | Notable news + replay numbers + whether they still support the current strategy version. Do NOT edit `strategy.md`/`params.json` here — that's the nightly job's call, with fuller context. |
