#!/bin/bash
# Cron wrapper: lightweight enqueue-only. Creates a stonks-schedule card
# instead of running a full agentTurn — no LLM call, near-zero event-loop
# footprint. Actual work (discovery_urgency_check.py) happens in
# stonks-queue-drain.
set -uo pipefail
cd /home/openclaw/.openclaw/workspace-trader-stonks

/home/openclaw/.npm-global/bin/openclaw workboard create \
  "[stonks-schedule] discovery-urgent $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --board stonks-schedule \
  --agent trader-stonks \
  --priority normal \
  --labels type:discovery-urgent \
  --notes "Run: python3 scripts/discovery_urgency_check.py from this cwd. Cash-deployment urgency check. If triggered:true it has already run discovery_scan.py internally and written discoveries/YYYY-MM-DD.md -- report discovery_run.top_written, do not re-run discovery_scan.py." \
  --json

echo "NO_REPLY"
exit 0
