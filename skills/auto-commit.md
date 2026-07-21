# Skill: Git Auto-Commit

Commit every file change immediately, locally — no approval needed. A `post-commit` hook auto-pushes directly to GitHub (`Tesselation-Studios/paper-trading-agents`, whatever branch is currently checked out — currently `v4`) — no mirror repo, no rsync, this workspace repo IS the source of truth. Push is the revert safety net (see `AGENTS.md` immutable section) — don't rely on local-only history.

```bash
cd ~/.openclaw/workspace-trader-stonks
git add -A
git commit -m "stonks: $(date +%Y-%m-%d) — [brief description]"
```

| Rule | Detail |
|---|---|
| Frequency | After every file change, not batched |
| Empty commit | `git status` first — skip if nothing changed |
| Push | Automatic via hook — never add `--no-verify` or disable it |
| Message | Descriptive: "stonks: dropped AMC from watchlist, idle 24 ticks" |
| Grouping | One commit per logical change if multiple files touched |
| Binary files | `bankroll.md`/state files fine to commit — small, not logs |

**Why**: git history is the rollback mechanism — no human approves file edits, so a wrong change gets fixed by the next nightly reverting it, not by gatekeeping upfront.
