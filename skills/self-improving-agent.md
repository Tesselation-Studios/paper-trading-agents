# Skill: Self-Improving Agent — Per-Signal Confidence

Score each signal independently and let the ones with a real track record carry more weight over time — not folding everything into one gut-feel conviction number.

## Score each signal you actually used, separately

On any BUY/SELL decision, for every signal that genuinely informed it, add an entry to `--features` shaped `{"direction": "bullish"|"bearish"|"neutral", "confidence": 0.0-1.0}` (per `scripts/signals.py`'s schema — see `skills/tool-invocation.md` for the exact `record_decision.py` syntax). Current real signal names: `technical` (RSI/MACD), `sentiment` (FinBERT via `state/sentiment_cache.json`), `regime` (`get_market_regime`), `macro` (`get_macro`), `fundamentals` (`get_fundamentals`), `insiders` (`get_insiders`), `congress` (`get_congress`), `social` (Reddit/StockTwits/Bluesky, `get_social` — `skills/social-sentiment.md`), `momentum` (cross-sectional rank, `skills/data-bus-fallback.md`'s `/momentum`), `narrative` (wiki synthesis pages from `wiki_search`/`stonks-worldview-sync`), `ml_signal` (trained win/loss classifier probability + explanation, `get_ml_signal`). `flow` isn't scored — the tool's disabled (not real options-flow data). Only include signals you actually checked — don't backfill a plausible-looking entry for one you didn't call.

`confidence` is *your* read on that specific signal given what it showed — not the final trade conviction. Keep them independent; `record_decision.py` reconciles them for you.

## Pre-trade: `reconcile` is where conviction comes from

`--conviction` isn't a number you guess at decision time. Before sizing/executing a BUY, run `python3 scripts/record_decision.py reconcile --features '...'` (same `--features` shape as above, no DB write, safe to call as many times as you want while still deciding) and use its `combined_confidence` as the `--conviction` you pass to the executor's BUY call — see `tick_prompt.md` step 8/9. That single executor call now also writes the decisions-table log directly (mechanized 2026-08-02, no separate `record_decision.py decision` call needed). `gate_conviction` (`scripts/executor.py`) only checks this against a flat sanity floor (`params.json: risk.conviction_floor`) — it catches a broken/zero score, it isn't a bar the number needs to clear. Real entry quality is the gestalt reasoning behind the number, not the number itself.

You may still deviate from the reconciled number (size up/down) if you have a concrete reason not captured in a scored signal — say why in `--rationale`, same as always.

## The reconciled read, echoed post-trade too

The pre-trade `reconcile` call above (recommendation, confidence, per-signal detail including `scorecard_multiplier`, see below) is your one look at the combined read before you commit to a `--conviction` — the mechanized executor-call logging (2026-08-02) doesn't echo a second `reconciled` confirmation back afterward the way the standalone `record_decision.py decision` CLI used to. If you passed an explicit `--conviction` that deviated from the pre-trade reconcile, that's expected and fine, and still worth saying in `--rationale` — the disagreement itself is useful signal for the scorecard, it's just not auto-echoed anymore.

## Signal scorecard — real track record, not a guess

`scripts/signal_scorecard.py` computes each signal's actual empirical hit rate from `state/trader.db`'s `training_examples` table (did that signal's stated direction match the eventual win/loss), writing `state/signal_scorecard.json`. It runs off-hours (`skills/off-hours-research.md`), not every tick — it's a stats job, not something that needs to be fresh to the minute.

**Signals with fewer than 10 labeled examples are marked `insufficient_data`, not given a hit rate.** Below that threshold, `reconcile_signals()`'s output is identical to the fixed-weight baseline for that signal — don't read anything into it. Check `state/signal_scorecard.json` periodically (off-hours is a good time) to see when a signal crosses the threshold — that's when its `scorecard_multiplier` starts moving off 1.0 and its weight in the reconciled read starts actually shifting based on whether it's been right.

This is deliberately not a learned ML model. It's the simplest thing that's honest about small-sample sizes — see `scripts/signals.py`'s module docstring for why.
