# Skill: Nightly Tick-Replay Practice

A historical practice session, not a live tick. Every weeknight, `scripts/prepare_tick_replay.py` runs automatically (a separate cron, before yours) and writes `research/tick_replay_latest.json` — one real past trading day from your own history, sampled every 15 minutes, plus a handful of peer tickers you didn't hold that day. You don't run the prep script yourself; by the time your turn starts, the file is already there.

## The point of this

Compressing extra practice reps into the evening — reading real data, forming real opinions, and leaving behind real memories, the same way a live tick does, just against a day that already happened instead of one still unfolding. This is deliberately NOT the numerical ML signal (`get_ml_signal`) — that's a separate model training on its own cadence. This is about your own judgment getting reps.

## What's in `tick_replay_latest.json`

- `date`: the historical day being replayed.
- `universe`: tickers with usable data that day (may be smaller than today's full watchlist — some names are recently-added and don't have deep history yet).
- `ticks`: chronological list of `{time, snapshot: {TICKER: {close, rsi_14, macd_hist, volume_ratio}}}` at 15-min intervals, market hours only.
- `actual_eod_closes`: what each ticker actually closed at that day — don't look at this until you've made your way through the ticks, same discipline as not peeking at the answer key.
- `alternative_candidates`: for each ticker in `universe`, a few real price/volume-band-matched peers you did NOT hold that day, with their day's open→close return — e.g. `alternative_candidates.BFH.DXCM` tells you what DXCM (same price band as BFH) actually did that day. Use these to ask "would this peer have beaten what I actually did with the one I held" — including when what you actually did was nothing (HOLD counts as a real decision to evaluate against).

## What to do

Walk through `ticks` in order as if it were a live session: for each timestamp, decide BUY/SELL/HOLD per ticker using the same judgment you'd apply live, and track a virtual $10,000 starting portfolio yourself (plain arithmetic in your own response — no script needed, no `executor.py`, no real trade). This is technicals-only, on purpose — no news/sentiment/congress feed exists for arbitrary past dates, so don't fabricate a catalyst that isn't in the data.

**Do not call any live-data or trade-placing tools during this** — `get_quote`, `get_technical_scan`, `executor.py`, anything that reflects the CURRENT moment would be wrong here and misleading to reason from. Your tools for this session are deliberately restricted to reading the prep file, your wiki/memory, and writing — nothing that could touch a real trade or real current data.

## When you reach the end of the day

This is judgment and discipline practice, not signal-mining — finding a numerical edge in technical features is the ML trainer's job (`get_ml_signal`), not yours. Don't try to reverse-engineer some hidden pattern that would have predicted a peer's move; reflect on how you read and acted on the data you actually had.

1. Compare your simulated day's outcome to `actual_eod_closes` — where did your read match reality, where didn't it.
2. **Consistency**: if you saw this same setup again, would you make the same call? Where did you hesitate or second-guess something you shouldn't have, or vice versa?
3. **Calibration**: did the conviction you assigned in the moment actually track what happened — were your higher-conviction calls the ones that worked out?
4. **Discipline**: did you size, hold, and exit in line with your own stated risk rules, or drift from them?
5. Look at `alternative_candidates` — would any of them genuinely have outperformed what you actually held? Be honest if the answer is no; "I'd make the same call again" is a real, useful finding, not a null result. If one genuinely would have, ask only whether something in the data you already had (the tick's own RSI/MACD/volume, portfolio state) pointed there and your read missed it — a grounded "I should have caught that." If it wouldn't have been visible in that data at all, leave it — that's not a gap in your judgment.
6. Write a real reflection — what surprised you, what you'd do differently, anything worth remembering — to that day's journal entry (or append a dated section if one already exists) and to wiki via `wiki_apply` if something feels durable enough to inform future live decisions, not just this one replayed day.

Multiple replay nights accumulate across different historical days (tracked in `state/tick_replay_progress.json`, most recent day first) — so over time this becomes real, varied practice, not the same day relived.
