#!/bin/bash
# Cron wrapper: gates on --check-health exit code so healthy runs are silent
# (NO_REPLY token) and unhealthy runs deliver a readable summary.
set -uo pipefail
cd /home/openclaw/.openclaw/workspace-trader-stonks

output=$(python3 scripts/discovery_daemon.py --check-health 2>&1)
rc=$?

if [ "$rc" -eq 0 ]; then
  # Healthy — suppressed by OpenClaw's NO_REPLY token
  echo "NO_REPLY"
else
  # Unhealthy — parse JSON and produce readable output
  healthy=$(echo "$output" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('last_cycle_status','?')); print(d.get('last_cycle_completed_at','?')); print(d.get('sentiment_worker_ok','?'))" 2>/dev/null)
  if [ $? -eq 0 ]; then
    IFS=$'\n' read -r status completed sentiment <<< "$healthy"
    echo "⚠️ Discovery daemon health check — last cycle: $status at $completed, sentiment worker: $sentiment"
  else
    echo "⚠️ Discovery daemon health check failed: $output"
  fi
fi

# Always exit 0 so OpenClaw marks the run "ok" (no failure-alert spam).
# Detection is delivered through readable output, not the error path.
exit 0
