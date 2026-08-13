# Skill: Research Escalation

Companion to `skills/decision-tree.md` — this is what happens when the tree *doesn't* have a clean answer and it's worth spending money to find out more. Run from the `stonks-research-escalation` cron (separate cadence from the live tick — a `sessions_send` round-trip can run minutes, which doesn't fit the tick's 290s budget), not from `tick_prompt.md` itself.

## Trigger

A watchlist candidate qualifies when `scripts/research_escalation.py select` returns it — mechanically: `tree_match_state` is `watch`, `insufficient_data`, or `no_match` (i.e. NOT a clean `active` match — that case doesn't need research, the tree already resolved it) AND `interest_score >= watchlist.interest_min_score` (not worth the cost on a candidate nobody's interested in) AND outside the research cooldown (`watchlist.research_escalation.cooldown_hours`, default 6h — don't re-research the same name every cycle).

`tree_match_state`/`interest_score` are set live by Stan's own tick evaluation (`tick_prompt.md` step 8's fast-path check, persisted via `trader_write.py watchlist-mark-evaluated --tree-match`) — this cron never re-derives them, it only consumes what the tick already recorded.

## What to do

1. `python3 scripts/research_escalation.py select` — returns the candidate list plus `cap` (max candidates this run, default 3 — this is the most expensive signal in the pipeline, a real LLM-to-LLM conversation, not a websearch) and `max_rounds` (max back-and-forth per candidate, default 2).

2. For each candidate, up to `cap`: dispatch a **narrow, specific** first question via `sessions_send` to `researcher` — not "research this stock," but something like "what's the latest news/catalyst on `<ticker>` in the last 1-2 weeks, and does anything stand out as a reason to buy or avoid it right now?" Include the candidate's own signals (price, sentiment, `news_headline`, sector, `tree_match_state`) as context so researcher isn't starting cold.

3. Read the reply. Decide, per the exchange (not a fixed script):
   - **Enough to act** — a clear catalyst/red-flag emerged, confidence is high enough to note a direction. Stop here.
   - **One specific follow-up worth asking** — the reply raised a concrete, narrower question ("you mentioned a pending FDA decision — what's the timeline?"). Ask it. Capped at `max_rounds` total exchanges per candidate — after that, conclude with whatever you have, don't keep digging.
   - **Not worth it** — nothing came back that changes the picture, or the name genuinely isn't interesting on closer look. Stop here too; a firm "pass" is a real, useful conclusion, not a failure.

4. Record the outcome: `python3 scripts/trader_write.py watchlist-record-research --ticker X --confidence <0.0-1.0, your read of how strong the case is either direction> --note '<2-3 sentence takeaway>'`. This is what the next live tick sees as an additional signal (via `trader_query.py watchlist`) without waiting on researcher itself — `research_confidence`/`research_note` are just columns on the watchlist row now.

5. If the conversation surfaced something durable — a real thesis, a pattern worth remembering beyond this one candidate — write it to that ticker's wiki entity page (`entities/<ticker>.md`, `pageType: entity`) via `wiki_apply`, citing the researcher exchange as the source. See `tick_prompt.md` step 9's "Writing durable knowledge to the wiki" for the same discipline applied to thesis-backed trades. Not every research pass needs a wiki write — only when there's a real, evidence-backed conclusion worth not re-deriving next time.

Not a trading tick — no `active.md` update, no `executor.py` calls. A short note in `off_hours/YYYY-MM-DD.md` (which tickers, what you concluded) is enough, same posture as `skills/freeform-discovery.md`.

## Format compatibility

`watchlist-record-research` writes directly to `trader_db.py`'s `watchlist_candidates` row (`last_researched_at`/`research_confidence`/`research_note`) — no separate file, picked up automatically by `trader_query.py watchlist` and by `research_escalation.py`'s own cooldown check on the next selection run.
