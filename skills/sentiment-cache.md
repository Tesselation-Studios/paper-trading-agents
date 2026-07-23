# Skill: Sentiment Cache

Real per-ticker sentiment (Alpaca News -> FinBERT, refreshed every ~15min by the `stonks-sentiment-refresh` cron via `scripts/news_collector.py`) lives in `state/sentiment_cache.json`. Read it directly — don't use `curl localhost:5000/sentiment` (see `skills/data-bus-fallback.md`); that endpoint was structurally broken (confirmed 2026-07-23: FinBERT/Praesentire are both healthy, but nothing ever wrote the per-ticker cache keys it reads from).

```bash
cat state/sentiment_cache.json
```

Shape: `{"generated_at": ISO8601, "tickers": {TICKER: {"avg_sentiment": -1..1, "article_count": N, "latest_headline": "...", "latest_source": "alpaca_news"}}}`.

- A ticker missing from `tickers` means no recent Alpaca News for it — genuinely no signal, not an error. Don't fabricate a score.
- If `generated_at` is more than ~45min old (3x refresh cadence), treat as stale — note it in active.md, proceed without blocking, same as any best-effort data source.
- Feed `avg_sentiment`/`article_count` into `record_decision.py --features '{"sentiment": {...}}'` on BUY/SELL (see `skills/tool-invocation.md`).
