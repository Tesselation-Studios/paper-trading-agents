# Skill: LLM Replay & Prompt Iteration (Phase 7a)

`scripts/llm_replay.py` asks Stan's REAL model for a real decision on each simulated historical day (daily cadence — Alpaca only gives 3 days of 5-min bars, no accumulated intraday history exists for this ticker universe), unlike `replay_check.py`'s hand-coded RSI/MACD proxy logic.

```bash
python3 scripts/llm_replay.py --days 80 --tickers NVDA GME
```

Reports the LLM-driven result alongside the hand-coded proxy's result on the same window — a large disagreement between the two is itself a signal worth investigating (proxy logic drifting from real judgment, or vice versa).

**Baseline-only right now — variant/prompt-iteration testing is NOT implemented.** `openclaw agent --agent trader-stonks --message ...` always loads the REAL `AGENTS.md`/`TOOLS.md`/etc. from disk (confirmed empirically, not something the message text controls) — testing a candidate `TOOLS.md` would mean swapping the real file on disk, which is genuinely dangerous while live trading is active: a concurrent live tick firing mid-swap would read the candidate/broken file for a REAL decision. Needs a real safety mechanism (hard market-hours check + a lock file live ticks actually check, at minimum) before it's built. Don't build the file-swap path without that in place first.

**Multi-day validation, not single-day** (adopted from `paper-trading-rebuild/fusion-review.md`'s critique of the sister repo's prompt-sweep design): a single lucky simulated day is a guaranteed overfitting trap. Any future variant comparison needs to win across the whole tested window, same "robust across the whole test" bar `replay_check.py --split-window` already established for the v1.1 promotion — not reinvented, directly reused.

**Cost**: real observed usage ≈ 25-30K tokens/call (mostly cached context reload), one call per simulated day (not per ticker — see `make_llm_trader`'s per-day memoization). A full ~25-day pass is cheap, well under $1.

Feeds `scripts/evolution_proposal.py` exactly like `sweep_thresholds()` does for params — any future variant win gets written up as a proposal citing the real comparative numbers, `review_required` tier (unchanged, correct), never auto-applied.
