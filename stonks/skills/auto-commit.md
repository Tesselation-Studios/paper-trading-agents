# Skill: Git Auto-Commit

**Purpose:** Every time you modify a file in your workspace, commit it immediately, locally. Git history is the safety net — no approval needed for changes.

## Behavior

**After any file write/edit:**
```bash
cd ~/.openclaw/workspace-trader-stonks
git add -A
git commit -m "stonks: $(date +%Y-%m-%d) — [brief description of what changed]"
```

**Rules:**
- Commit after every file change, not batched
- If the commit would be empty (no changes), skip it — check `git status` first
- This is a **local-only** repo for now — no remote push. Don't add a remote or push unless explicitly asked.
- Commit messages should be descriptive: "stonks: dropped AMC from watchlist, idle 24 ticks"
- If you modified multiple files in one session, one commit per logical change is fine

## Why

- Git history is the rollback mechanism. Every change is reversible.
- No human needs to approve file edits. You're a self-improving trader.
- The nightly maintenance already does this — this skill extends it to tick-level changes.
- If a change was wrong, the next nightly can revert it.

## Edge Cases

- **Empty commit**: `git commit` will error if nothing changed. Check `git status` first.
- **Large binary files** (`trader.db`): fine to commit, it's small (SQLite state, not logs).
