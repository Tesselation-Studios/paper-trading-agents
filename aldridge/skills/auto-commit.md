# Skill: Git Auto-Commit

**Purpose:** Every time you modify a file in your workspace, commit it immediately. Git history is the safety net — no approval needed for changes.

## Behavior

**After any file write/edit:**
```bash
cd /home/openclaw/paper-trading-agents
git add aldridge/  # or the specific file
git commit -m "aldridge: $(date +%Y-%m-%d) — [brief description of what changed]"
git push
```

**Rules:**
- Commit after every file change, not batched
- If the commit would be empty (no changes), skip it
- Push at the end of each tick, not after every commit (batched pushes are OK)
- Commit messages should be descriptive: "aldridge: tightened stop on GOOGL from -5% to -4.5%"
- If you modified multiple files in one session, one commit per logical change is fine

## Why

- Git history is the rollback mechanism. Every change is reversible.
- No human needs to approve file edits. You're a self-improving trader.
- The nightly maintenance already does this — this skill extends it to tick-level changes.
- If a change was wrong, the next nightly can revert it.

## Edge Cases

- **SSH timeout**: If `git push` fails, the commit still exists locally. Push will retry next tick.
- **Empty commit**: `git commit` will error if nothing changed. Check `git status` first.
- **Merge conflicts**: You shouldn't get these since you're the only one editing. If you do, `git add` the resolved file and commit.