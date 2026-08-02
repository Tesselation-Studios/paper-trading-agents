# Skill: Nightly Tick-Replay Practice

A historical practice session, not a live tick. `scripts/prepare_tick_replay.py` runs automatically (a separate cron, before yours) and writes `research/tick_replay_latest.json`. You don't run the prep script yourself; by the time your turn starts, the file is already there.

## The point of this

Compressing extra practice reps into the evening — reading real data, forming real opinions, and leaving behind real memories, the same way a live tick does, just against a day that already happened instead of one still unfolding. This is deliberately NOT the numerical ML signal (`get_ml_signal`) — that's a separate model training on its own cadence. This is about your own judgment getting reps. Live trading only happens M-F 9:30-4:00 ET; this is how you get better during all the hours the market is closed.

## Two modes — check `mode` at the top of the file first

- **`mode` absent, or `"single-day"`** (the original mode): a fresh $10,000, one isolated day, no continuity with any other replay night. You track the virtual portfolio yourself in prose (see "Single-day mode" below) — no script writes anything for you.
- **`mode: "chained"`**: this file is one day in a multi-day `backtest_session.py` chain (`session_id` field tells you which one). You have a real, persistent simulated portfolio (`portfolio.cash`, `portfolio.open_positions`) that was carried forward from the previous simulated day and will carry forward to the next one — a position you open today can still be open weeks of simulated-time later. Real BUY/SELL calls go through `scripts/replay_order.py` (see "Chained mode" below), which runs them through the exact same gate chain (`executor.run_gates`) and writes to the exact same `trader_db.py` schema live trading uses — just pointed at this session's own isolated DB file, never `state/trader.db`. This is what makes a replayed day feed the ML classifier and signal scorecard the same way a real trade does, not just a journal entry.

Both modes exist on purpose and serve different goals: single-day for variety of judgment reps across many different unrelated days, chained for practicing the harder skill of holding a position across time and seeing how a multi-day/week thesis actually plays out.

## What's in `tick_replay_latest.json` (both modes)

- `date`: the historical day being replayed.
- `universe`: tickers with usable data that day (may be smaller than today's full watchlist — some names are recently-added and don't have deep history yet).
- `ticks`: chronological list of `{time, snapshot: {TICKER: {close, rsi_14, macd_hist, volume_ratio}}}` at 15-min intervals, market hours only.
- `actual_eod_closes`: what each ticker actually closed at that day — don't look at this until you've made your way through the ticks, same discipline as not peeking at the answer key.
- `alternative_candidates`: for each ticker in `universe`, a few real price/volume-band-matched peers you did NOT hold that day, with their day's open→close return — e.g. `alternative_candidates.BFH.DXCM` tells you what DXCM (same price band as BFH) actually did that day. Use these to ask "would this peer have beaten what I actually did with the one I held" — including when what you actually did was nothing (HOLD counts as a real decision to evaluate against).

Chained mode adds:
- `session_id`: pass this to every `replay_order.py`/`backtest_session.py` call this turn.
- `portfolio.cash`, `portfolio.portfolio_value`, `portfolio.open_positions` (each with `ticker, shares, entry_price, entry_time, sector, thesis`): what you're actually holding coming into today, same role live `tick_prompt.md` positions read plays at the top of a live tick.

## Single-day mode: what to do

Walk through `ticks` in order as if it were a live session: for each timestamp, decide BUY/SELL/HOLD per ticker using the same judgment you'd apply live, and track a virtual $10,000 starting portfolio yourself (plain arithmetic in your own response — no script needed, no `executor.py`, no `replay_order.py`, no real trade). **Do not call any live-data or trade-placing tools** — `get_quote`, `get_technical_scan`, `executor.py`, anything that reflects the CURRENT moment would be wrong here and misleading to reason from.

## Chained mode: what to do

Same judgment process, but real writes: at each tick, decide BUY/SELL/HOLD per ticker in `portfolio.open_positions` plus anything new from `universe` worth considering. Score signals and reconcile conviction exactly like a live tick (`skills/self-improving-agent.md`, `scripts/record_decision.py reconcile`). For BUY/SELL, call:

```
python3 scripts/replay_order.py --session-id <session_id> --action BUY --ticker TICKER \
    --qty N --price <tick's close> --timestamp <tick's time, that DATE, ISO8601 tz-aware, e.g. 2026-06-01T10:30:00-04:00> \
    --conviction <combined_confidence> --sector "..." --thesis "..." [--play-type standard|long|conviction] \
    [--features '{"technical": {"direction": "bullish", "confidence": 0.7}, ...}']

python3 scripts/replay_order.py --session-id <session_id> --action SELL --ticker TICKER \
    --qty N --price <tick's close> --timestamp <same format> --close-reason "..."
```

Use the tick's own timestamp, not today's real date — this is what lets `gate_hours` judge the order against the simulated moment instead of the real wall clock. A HOLD needs no call at all. A rejection returns the same `{"error": "guardrail: ...", "gates": [...]}` shape live `executor.py` does — that's real gate feedback about this decision, not a friendlier simulated version of it; treat a block the same way you would live (reconsider, don't route around it).

You may walk multiple ticks — and, if the day's data supports it, evaluate whether to extend a hold rather than close by end of day; unlike single-day mode, "hold through today" is a real, meaningful choice here since the position will still be open tomorrow's simulated day.

**When you finish the day**: write your journal entry (see "When you reach the end of the day" below), and only *after* that write succeeds, mark the day complete:

```
python3 scripts/backtest_session.py complete-day <session_id> --date <date>
```

This is two-phase on purpose — the day was already reserved as in-progress before your turn started (`start_day()`, done by the prep script). If your turn errors or times out before you call `complete-day`, the *same* day gets prepared again next time, not silently skipped, so don't call `complete-day` early or speculatively.

## Point-in-time signal availability (chained mode especially — matters far more once you're replaying days more than ~5-6 days old)

- **`news_cache`** is real but shallow (~5-6 days of RSS). For dates within that window, check it first.
- **For anything older — news, insider Form-4 filings, congressional trades, social sentiment** — these are NOT gone, they're just not in the live MCP tools' snapshot form. Reach for `web_search`, `tavily_search` (`topic: "finance"`/`"news"`), `tavily_extract`, or `browser`, the same tools `skills/freeform-discovery.md` already uses for live catalyst hunting — but **date-scope every query to the simulated day/month explicitly** (e.g. "SOFI news June 2026", not "SOFI news"). SEC EDGAR and House/Senate stock-disclosure sites are permanent public record, searchable by exact historical date, and are actually *more* reliable historically than the live feed.
- **Look-ahead-bias guard, every time**: a web search is not a clean point-in-time archive. A retrospective article written long after the simulated date, a search engine surfacing recent commentary next to old results, a Reddit thread that kept running for months past the date you're replaying — any of these can leak information you wouldn't have had live. Before using a result, check its actual publish/event date against the day you're replaying. If it references anything after that date, it's contaminated for this decision — discard it, don't use it, don't let it color your read even subconsciously.
- **`regime` (`get_market_regime`) and `ml_signal` (`get_ml_signal`) are genuinely, structurally unavailable for a historical date** — not because the data doesn't exist, but because both are outputs of models trained on data available *up to now*; re-running today's model against a historical date would leak information the live version of you wouldn't have had at that time. **Just omit these from `--features` entirely — do not fabricate a low-confidence placeholder for them.** `scripts/signals.py`'s `reconcile_signals` already handles an absent signal correctly (it's excluded from the weighted combination, not counted as a strike against it) — inventing a weak/neutral entry for something you have no real read on would make your recorded conviction *less* accurate, not more honest.

## Self-improvement: surface gaps, don't just work around them

If a replay session (either mode) surfaces something that isn't about this one trade — a tool you needed didn't exist, a script errored or behaved unexpectedly, a gate did something surprising, a pipeline silently produced no data — don't just note it in your own head and move past it. Add a line to `tasks/pending.md` (format: `- [ ] YYYY-MM-DD (tick-replay): description`) so it survives into the next nightly-maintenance/nightly-learning/weekly-review run instead of rotting in a journal entry nobody re-reads. This is exactly the same mechanism live trading uses for the same purpose.

## When you reach the end of the day

This is judgment and discipline practice, not signal-mining — finding a numerical edge in technical features is the ML trainer's job (`get_ml_signal`), not yours. Don't try to reverse-engineer some hidden pattern that would have predicted a peer's move; reflect on how you read and acted on the data you actually had.

1. Compare your simulated day's outcome to `actual_eod_closes` — where did your read match reality, where didn't it.
2. **Consistency**: if you saw this same setup again, would you make the same call? Where did you hesitate or second-guess something you shouldn't have, or vice versa?
3. **Calibration**: did the conviction you assigned in the moment actually track what happened — were your higher-conviction calls the ones that worked out?
4. **Discipline**: did you size, hold, and exit in line with your own stated risk rules, or drift from them?
5. Look at `alternative_candidates` — would any of them genuinely have outperformed what you actually held? Be honest if the answer is no; "I'd make the same call again" is a real, useful finding, not a null result. If one genuinely would have, ask only whether something in the data you already had (the tick's own RSI/MACD/volume, portfolio state) pointed there and your read missed it — a grounded "I should have caught that." If it wouldn't have been visible in that data at all, leave it — that's not a gap in your judgment.
6. Write a real reflection — what surprised you, what you'd do differently, anything worth remembering — to that day's journal entry (or append a dated section if one already exists) and to wiki via `wiki_apply` if something feels durable enough to inform future live decisions, not just this one replayed day.
7. **Chained mode only**: after the journal write above succeeds, call `backtest_session.py complete-day` (see "Chained mode" above) — this is what advances the chain to the next simulated day.

Single-day-mode nights accumulate across different historical days (tracked in `state/tick_replay_progress.json`, most recent day first) — real, varied practice, not the same day relived. Chained-mode nights accumulate as consecutive days inside a `state/backtest/<session_id>.db` session (tracked in `state/backtest/<session_id>.manifest.json`) — real, continuous practice at holding a thesis across simulated time. Both run side by side; neither replaces the other.
