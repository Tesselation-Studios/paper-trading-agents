# Skill: Off-Hours Research Routine

Runs daily 20:00 ET via the `stonks-off-hours` cron — market is closed, no trading, don't call `scripts/executor.py`.

## Step 1: News + sentiment refresh
Read `strategies/watchlist.md` + `positions/*.md` for your current tickers, then:
```bash
python3 scripts/news_collector.py <space-separated tickers>
```
Fetches fresh RSS articles into the shared `public.news_cache` Postgres table (additive-only, `ON CONFLICT DO NOTHING`), reports sentiment for your tickers over the last 24h. Note real catalysts, not noise — carries into tomorrow's ticks.

## Step 2: Replay check
```bash
python3 scripts/replay_check.py <same tickers>
```
Backtests current hardened rules (MACDh-flip exit, regime-gated entries — see `strategy.md`) against pre-promotion rules over ~200 days of real Alpaca history. Same simple entry logic in both variants — isolates whether the rule change itself helped. This is the empirical check that should inform (not replace) the nightly Evolve step's confidence in a rule.

Caveat: the entry logic is a simple RSI-band proxy, not your actual qualitative judgment — it tests the mechanical exit/gating rules, not full decision quality.

## Step 3: Brief log
Write 5-10 lines to `off_hours/YYYY-MM-DD.md`: notable news, the replay numbers (old vs new), and whether they still support the current strategy version. Do NOT edit `strategy.md`/`params.json` here — that's the nightly job's decision, with fuller context.
