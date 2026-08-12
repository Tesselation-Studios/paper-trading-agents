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

Do NOT call `wiki_search` yourself here. As of 2026-08-12, `wiki_search`
has a recurring multi-minute hang (seen across multiple agents/crons, not
specific to this one) that was force-aborting this entire cron on every
run — 9 consecutive failures before this was caught. Just pass the raw
sector/theme list to researcher in Step 2 (limited to what you're
actually exposed to — held or actively watchlisted, not every sector in
existence) and let it determine existing-vs-new pages itself as part of
its own `wiki_apply` workflow — it already needs that lookup to avoid
duplicating a page, so nothing is lost by not pre-checking here.

## Step 2: Delegate to researcher (this is the actual work — give it room)

Call `sessions_send` with `agentId: "researcher"` and `timeoutSeconds: 480`.
Write the message as a real research brief, not a one-liner — include:
- The sector/theme list from Step 1. Ask researcher to check for an
  existing synthesis page on each theme first (title/lookup,
  `confidence`/`status`/`updatedAt`) and extend it if one exists rather
  than starting over — a page updated in the last 1-2 days probably
  doesn't need fresh evidence tonight; one that's a week+ stale, or has
  open `questions`, is worth revisiting. Only write a first-look page for
  a theme with no existing page if you have a real reason (a position or
  serious watchlist candidate there, not curiosity).
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
