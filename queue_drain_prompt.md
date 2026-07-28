# Stonks Schedule — Queue Drain Loop

Mirrors Orchestrator's board-drainer pattern. Serializes non-critical
scheduled work (sentiment refresh, discovery-urgency check) into one lane
so it never competes with stonks-tick's live-trading loop.

1. `workboard_list(boardId="stonks-schedule", status="todo", limit=5)`
2. No cards → nothing to do, HEARTBEAT_OK.
3. Claim the highest-priority card (`workboard_claim`). Work it directly —
   no sub-agents, no session_spawn.
4. Dispatch on the card's label:
   - `type:sentiment-refresh` → run `python3 scripts/news_collector.py`
     (no args) from this workspace. Do NOT place trades, do NOT call
     scripts/executor.py. Report the script's "sentiment_cache_written_for"
     list in the completion note.
   - `type:discovery-urgent` → run `python3 scripts/discovery_urgency_check.py`
     from this workspace. If `"triggered": false`, note cash % and
     candidate count. If `"triggered": true`, report
     `discovery_run.top_written` and why it fired — do not re-run
     discovery_scan.py yourself, the check already ran it.
5. `workboard_complete(cardId, note=<summary above>)`. Script errors are
   fine — these are best-effort/fail-open refreshes; complete the card
   with the error noted, don't escalate on a single failure.
6. `session_yield()` — next tick picks up the next card.

Rules: one card per tick, session_yield between cards. Never claim more
than one card per invocation. This lane is for non-critical scheduled
refreshes only — never touch stonks-tick's cadence or HEARTBEAT.md's loop.

HEARTBEAT_OK
