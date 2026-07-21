#!/usr/bin/env bash
# sync-and-push.sh — Sync workspace-trader-stonks → paper-trading-agents/stonks/ and push to GitHub.
# Called as a post-commit hook. The workspace is the source of truth during trading hours;
# paper-trading-agents is the GitHub mirror for history/revert safety.
set -euo pipefail

WORKSPACE="/home/openclaw/.openclaw/workspace-trader-stonks"
MIRROR="/home/openclaw/paper-trading-agents"
MIRROR_SUBDIR="stonks"
LOG="/home/openclaw/projects/paper-trading-rebuild/logs/sync-push.log"
COMMIT_MSG="${1:-auto: sync from workspace}"

mkdir -p "$(dirname "$LOG")"

echo "[$(date)] sync-and-push: starting" >> "$LOG"

# Stage 1: rsync workspace → mirror/stonks/ (exclude git + transient)
rsync -a --delete \
  --exclude='.git/' \
  --exclude='trader.db' \
  --exclude='__pycache__/' \
  --exclude='openclaw-workspace-state.json' \
  --exclude='*.pyc' \
  "$WORKSPACE/" "$MIRROR/$MIRROR_SUBDIR/" >> "$LOG" 2>&1

# Stage 2: commit + push in mirror repo
cd "$MIRROR"
if git diff --quiet && git diff --cached --quiet; then
    # Check for untracked files
    if [ -z "$(git status --porcelain -- "$MIRROR_SUBDIR/")" ]; then
        echo "[$(date)] sync-and-push: nothing changed, skipping" >> "$LOG"
        exit 0
    fi
fi

git add "$MIRROR_SUBDIR/"
git commit -m "stonks: $COMMIT_MSG" >> "$LOG" 2>&1 || echo "[$(date)] nothing to commit" >> "$LOG"
git push origin main >> "$LOG" 2>&1 && echo "[$(date)] ✓ pushed to GitHub" >> "$LOG" || echo "[$(date)] ✗ push failed" >> "$LOG"

echo "[$(date)] sync-and-push: done" >> "$LOG"