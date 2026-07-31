# Skill: Self-Improving Agent — Per-Signal Confidence

Score each signal independently and let the ones with a real track record carry more weight over time — not folding everything into one gut-feel conviction number.

## Score each signal you actually used, separately

On any BUY/SELL decision, for every signal that genuinely informed it, add an entry to `--features` shaped `{"direction": "bullish"|"bearish"|"neutral", "confidence": 0.0-1.0}` (per `scripts/signals.py`'s schema — see `skills/tool-invocation.md` for the exact `record_decision.py` syntax). Current real signal names: `technical` (RSI/MACD), `sentiment` (FinBERT via `state/sentiment_cache.json`), `regime` (`get_market_regime`), `macro` (`get_macro`), `fundamentals` (`get_fundamentals`), `insiders` (`get_insiders`), `congress` (`get_congress`), `momentum` (cross-sectional rank, `skills/data-bus-fallback.md`'s `/momentum`), `narrative` (wiki synthesis pages from `wiki_search`/`stonks-worldview-sync`). `flow` isn't scored — the tool's disabled (not real options-flow data). Only include signals you actually checked — don't backfill a plausible-looking entry for one you didn't call.

`confidence` is *your* read on that specific signal given what it showed — not the final trade conviction. Keep them independent; `record_decision.py` reconciles them for you.

## Pre-trade: `reconcile` is where conviction comes from

`--conviction` isn't a number you guess at decision time. Before sizing/executing a BUY, run `python3 scripts/record_decision.py reconcile --features '...'` (same `--features` shape as above, no DB write, safe to call as many times as you want while still deciding) and use its `combined_confidence` as the `--conviction` you pass to both the executor's BUY call and the later `record_decision.py decision --conviction <same number>` log call for that same trade — see `tick_prompt.md` step 8. `gate_conviction` (`scripts/executor.py`) only checks this against a flat sanity floor (`params.json: risk.conviction_floor`) — it catches a broken/zero score, it isn't a bar the number needs to clear. Real entry quality is the gestalt reasoning behind the number, not the number itself.

You may still deviate from the reconciled number (size up/down) if you have a concrete reason not captured in a scored signal — say why in `--rationale`, same as always.

## The reconciled read, echoed post-trade too

`record_decision.py decision` also still echoes back `result.reconciled` after logging — same computation as the pre-trade `reconcile` call above (recommendation, confidence, per-signal detail including `scorecard_multiplier`, see below), just confirming what was actually stored. If you passed an explicit `--conviction` that deviated from the pre-trade reconcile, this echo won't match it — that's expected and fine, the disagreement itself is useful signal for the scorecard.

## Signal scorecard — real track record, not a guess

`scripts/signal_scorecard.py` computes each signal's actual empirical hit rate from `state/trader.db`'s `training_examples` table (did that signal's stated direction match the eventual win/loss), writing `state/signal_scorecard.json`. It runs off-hours (`skills/off-hours-research.md`), not every tick — it's a stats job, not something that needs to be fresh to the minute.

**Signals with fewer than 10 labeled examples are marked `insufficient_data`, not given a hit rate.** Below that threshold, `reconcile_signals()`'s output is identical to the fixed-weight baseline for that signal — don't read anything into it. Check `state/signal_scorecard.json` periodically (off-hours is a good time) to see when a signal crosses the threshold — that's when its `scorecard_multiplier` starts moving off 1.0 and its weight in the reconciled read starts actually shifting based on whether it's been right.

This is deliberately not a learned ML model. It's the simplest thing that's honest about small-sample sizes — see `scripts/signals.py`'s module docstring for why.
