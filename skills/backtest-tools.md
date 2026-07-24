# Skill: Backtest Tools — Testing a New Idea Without Touching Live Files

The tools behind every real test in the 2026-07-24 v1.6/v1.7/v1.8 iteration. Never edit `strategy.md`/`params.json`/`tick_prompt.md`/any `scripts/*.py` just to try something — every experiment below is a parameterized in-memory call. If a finding actually clears the bar, escalate per `skills/evolution-proposals.md` (strategy.md/params.json changes self-commit with evidence; anything else needs `evolution_proposal.py create`).

## `scripts/replay_check.py` — entry/exit rule variants

- `python3 scripts/replay_check.py --split-window` — the real promotion bar: Sharpe positive in BOTH halves independently, not one aggregate number. Run this before believing any result.
- `--sweep` — grid-search `stop_loss_pct`/`profit_target_pct` via `sweep_thresholds()`.
- `STRATEGY_BUILDERS` (dict, top of file) registers every variant. `make_trader(frames, variant, ...)` takes real keyword params — `stop_loss_pct`, `profit_target_pct`, `max_positions`, `scale_into_winners`, `scale_in_max_per_day`, `scale_in_max_multiple`. To test a new idea: add one lambda to `STRATEGY_BUILDERS` (and a matching entry in `VARIANT_LABELS`) with the params you want — no file elsewhere needs to change. That's exactly how `v1.7-gentle` (a 1.5x size cap instead of 3x) got tested tonight: one dict entry, run, read the result, done.
- Import it directly for a quick one-off instead of editing the file at all: `import replay_check; trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True, scale_in_max_multiple=2.0)`.

## `scripts/universe_scan.py` — does this generalize, or did we miss something?

- `--strategy` (any `STRATEGY_BUILDERS` key), `--sample-size`, `--seed` (fixed seed = reproducible comparison across runs). Ranks a random ticker sample by Sharpe, excluding anything already held/watchlisted.
- Answers two different questions than `replay_check.py`: does the strategy generalize beyond the names we already picked, and were there better plays in the same window we never looked at.

## Price-tier (or any-axis) comparison pattern

Not a built-in flag — a technique, so it adapts to whatever you're actually curious about (price tier, a specific sector if the data supports it, anything). Fetch once, split with `universe_scan.filter_by_price_band()`, rank each bucket:

```python
import universe_scan, replay_check
sample = universe_scan.fetch_broad_universe(sample_size=250, seed="whatever")
frames = replay_check.fetch_history(sample)
bucket_a = universe_scan.filter_by_price_band(frames, 1.0, 50.0)
bucket_b = universe_scan.filter_by_price_band(frames, 50.0, 500.0)
for name, bucket in [("a", bucket_a), ("b", bucket_b)]:
    ranked = universe_scan.rank_tickers(bucket, exclude=set(replay_check.load_live_universe()),
                                         strategy_name="v1.1", top_n=1000)  # 1000 = "give me everything eligible"
    # compare mean/median Sharpe, % positive, across buckets
```

This is exactly how the $1-500 universe widening got decided — a random, unbiased sample beats testing only the names discovery already curated for you.

## `bankroll.py` — tunable dials

`LEAD_PROTECT_THRESHOLD`, `SCALE_IN_MIN_PNL_PCT`, `SCALE_IN_SIZE_FACTOR`, `SCALE_IN_MAX_MULTIPLE`, `UNIVERSE_MAX_PRICE_TIERS` are real, isolated constants — retune-able, not sacred. Test a candidate value via `make_trader()`'s matching override param where one exists (`scale_in_max_multiple`); for constants with no override yet, import `bankroll` in a scratch script and monkeypatch the module attribute for the test run only — never edit the live file to check a number.

## `scripts/llm_replay.py` — test actual wording changes with real judgment, not a mechanical proxy

Unlike everything above (hand-coded RSI/MACD if-statements), this asks your REAL model for a real decision on each simulated historical day. `--strategy-file <path>` tests a candidate `strategy.md` wording — safe to do directly, no file-swap risk: the candidate text gets embedded as a plain string into this script's own prompt, never read from disk by the agent framework (unlike `TOOLS.md`/`AGENTS.md`, which genuinely can't be safely variant-tested yet, see `skills/prompt-iteration.md`).

```bash
# baseline (reads the real live strategy.md)
python3 scripts/llm_replay.py --days 120
# candidate — write your variant to a scratch file first, e.g. research/candidate-2026-07-24.md
python3 scripts/llm_replay.py --days 120 --strategy-file research/candidate-2026-07-24.md
```

Compare the two `llm_replay` result blocks (same tickers/window either way). One day is a coincidence, not a finding — multi-day validation only, same promotion bar as everything else here. A winning candidate still only reaches the real `strategy.md` through the normal self-commit/evolution-proposal path (`skills/evolution-proposals.md`) — this script only tells you whether it's worth proposing, it never writes the live file itself.
