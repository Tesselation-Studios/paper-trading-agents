# Skill: Self-Improving Agent — Per-Signal Confidence

This skill was referenced in your config but never written until 2026-07-24. It's the home for scoring each signal independently and letting the ones with a real track record carry more weight over time — not folding everything into one gut-feel conviction number.

## Score each signal you actually used, separately

On any BUY/SELL decision, for every signal that genuinely informed it, add an entry to `--features` shaped `{"direction": "bullish"|"bearish"|"neutral", "confidence": 0.0-1.0}` (per `scripts/signals.py`'s schema — see `skills/tool-invocation.md` for the exact `record_decision.py` syntax). Current real signal names: `technical` (RSI/MACD), `sentiment` (FinBERT via `state/sentiment_cache.json`), `regime` (`get_market_regime`), `macro` (`get_macro`), `fundamentals` (`get_fundamentals`), `flow` (`get_flow`), `insiders` (`get_insiders`). Only include signals you actually checked — don't backfill a plausible-looking entry for one you didn't call.

`confidence` is *your* read on that specific signal given what it showed — not the final trade conviction. Keep them independent; `record_decision.py` reconciles them for you.

## Pre-trade: `reconcile` is now where conviction comes from

2026-07-27: before this, `--conviction` was a plain number you guessed at decision time — the reconciled score existed but was purely informative, computed *after* the trade. Now it's the other way around: before sizing/executing a BUY, run `python3 scripts/record_decision.py reconcile --features '...'` (same `--features` shape as above, no DB write, safe to call as many times as you want while still deciding) and use its `combined_confidence` as the `--conviction` you pass to both the executor's BUY call and the later `record_decision.py decision --conviction <same number>` log call for that same trade — see `tick_prompt.md` step 8. This is what feeds `gate_conviction`'s floor check (`scripts/executor.py`), which as of the same date is a *dynamic* floor (see `deployment_pressure.py`) — the number you compute here is a real gate input, not decoration.

You may still deviate from the reconciled number (size up/down) if you have a concrete reason not captured in a scored signal — say why in `--rationale`, same as always.

## The reconciled read, echoed post-trade too

`record_decision.py decision` also still echoes back `result.reconciled` after logging — same computation as the pre-trade `reconcile` call above (recommendation, confidence, per-signal detail including `scorecard_multiplier`, see below), just confirming what was actually stored. If you passed an explicit `--conviction` that deviated from the pre-trade reconcile, this echo won't match it — that's expected and fine, the disagreement itself is useful signal for the scorecard.

## Signal scorecard — real track record, not a guess

`scripts/signal_scorecard.py` computes each signal's actual empirical hit rate from `state/trader.db`'s `training_examples` table (did that signal's stated direction match the eventual win/loss), writing `state/signal_scorecard.json`. It runs off-hours (`skills/off-hours-research.md`), not every tick — it's a stats job, not something that needs to be fresh to the minute.

**Signals with fewer than 10 labeled examples are marked `insufficient_data`, not given a hit rate.** As of 2026-07-24 every signal is still below that threshold (11 labeled rows total across all signals combined) — don't read anything into today's numbers, and don't be surprised if `reconcile_signals()`'s output looks identical to the pre-scorecard baseline for a while yet. Check `state/signal_scorecard.json` yourself periodically (off-hours is a good time) to see when signals actually cross the threshold — that's when their `scorecard_multiplier` starts moving off 1.0 and their weight in the reconciled read starts actually shifting based on whether they've been right.

This is deliberately not a learned ML model. It's the simplest thing that's honest about small-sample sizes — see `scripts/signals.py`'s module docstring for why.
