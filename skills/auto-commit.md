# Skill: Git Auto-Commit

Commit every file change immediately, locally — no approval needed. A `post-commit` hook auto-pushes directly to GitHub (`Tesselation-Studios/paper-trading-agents`, whatever branch is currently checked out — currently `v4`) — no mirror repo, no rsync, this workspace repo IS the source of truth. Push is the revert safety net (see `AGENTS.md` immutable section) — don't rely on local-only history.

```bash
cd ~/.openclaw/workspace-trader-stonks
git add -A
git commit -m "stonks: $(date +%Y-%m-%d) — [brief description]"
```

| Rule | Detail |
|---|---|
| Frequency | After every *real* file change, not batched |
| Empty commit | `git status` first — skip if nothing changed |
| Push | Automatic via hook — never add `--no-verify` or disable it |
| Message | Descriptive: "stonks: dropped AMC from watchlist, idle 24 ticks" |
| Grouping | One commit per logical change if multiple files touched |
| Binary files | `bankroll.md`/state files fine to commit — small, not logs |

**Don't manufacture a change just to have something to commit** (2026-07-27): `positions/*.md` used to get rewritten with fresh price/P&L every tick purely because a number ticked — that's not a real change, it's noise, and every rewrite triggered this hook's fetch+merge+push against the shared remote roughly every 5 minutes for zero new information (see `tick_prompt.md` step 9 — position files are thesis storage now, price lives in Alpaca, not duplicated here). If the only thing different about a file since last commit is a live-derivable number or a timestamp, that's not a real change — don't write it, don't commit it.

**Why**: git history is the rollback mechanism — no human approves file edits, so a wrong change gets fixed by the next nightly reverting it, not by gatekeeping upfront. That only works if the history is signal, not noise — a real change buried in hundreds of price-drift commits is as hard to find as no history at all.
