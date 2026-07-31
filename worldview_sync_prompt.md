# Worldview Sync — Stonks (evening, market closed, no trading)

The market is closed. You CANNOT and MUST NOT place trades, submit orders, or
take any action against the real (paper) account during this routine — this
is research delegation only, not a tick. Do not call scripts/executor.py.

**Purpose**: build standing, evolving narratives about sectors/macro themes
relevant to your current exposure — not per-ticker discovery (that's
`stonks-freeform-discovery`'s job at 08:00) and not a daily recap (that's
the nightly journal's job). This is the slow layer: a thesis you write
tonight should still be there, and get *revised* rather than duplicated,
when you or `researcher` come back to it in three days.

## Step 1: Gather context (2 min)

Read `strategies/watchlist.md` and `positions/*.md` to get your current
sectors and tickers. Group by sector/theme — you're briefing `researcher`
on themes, not a raw ticker list.

For each sector/theme you're actually exposed to (held or actively
watchlisted, not every sector in existence), run `wiki_search` for it.
Note which already have a synthesis page (and its `confidence`/`status`/
`updatedAt`) versus which have none yet. A page updated in the last 1-2
days probably doesn't need revisiting tonight; one that's a week+ stale,
or has open `questions`, is a good candidate to send back to `researcher`.

## Step 2: Delegate to researcher (this is the actual work — give it room)

Call `sessions_send` with `agentId: "researcher"` and `timeoutSeconds: 480`.
Write the message as a real research brief, not a one-liner — include:
- The sector/theme list from Step 1, split into "revisit" (existing wiki
  page, needs fresh evidence) vs "new" (no page yet, worth a first look
  only if you have a real reason — a position or serious watchlist
  candidate in that sector, not curiosity).
- For "revisit" items: the existing page's title/lookup and current
  confidence/status, so researcher extends it rather than starting over.
- Explicit instruction: use `wiki_apply synthesis "<title>"` with the
  **same title** as any existing page on that theme (favor updating over
  duplicating — same discipline as your own wiki-writing in tick_prompt.md
  step 9), cite real sources via `--source-id`, set `--confidence`
  honestly, and use `--contradiction`/`--question` when new evidence
  conflicts with or complicates the existing thesis rather than silently
  overwriting it. Run `wiki_lint` after writing.
- Keep the ask bounded — 3-5 themes max per run, not the whole universe.
  Depth over breadth; a rushed narrative is worse than an absent one.

## Step 3: Log the outcome (2 min)

Write 3-5 lines to `off_hours/YYYY-MM-DD.md`:
- Which themes you asked researcher to cover (revisit vs new)
- What researcher reported back (new pages written, existing pages
  updated, anything it flagged as a contradiction or open question)
- Anything that seemed off (researcher timed out, wiki_lint reported
  issues, a theme researcher couldn't find real evidence for)

Not a trading tick — no `active.md` update, no journal entry (that's the
16:30 nightly job's role).

HEARTBEAT_OK
