#!/bin/bash
# Cron wrapper: gates on --check-health exit code so healthy runs are silent
# (NO_REPLY token) and unhealthy runs deliver the JSON via announce.
set -uo pipefail
cd /home/openclaw/.openclaw/workspace-trader-stonks

output=$(python3 scripts/discovery_daemon.py --check-health 2>&1)
rc=$?

if [ "$rc" -eq 0 ]; then
  # Healthy — suppressed by OpenClaw's NO_REPLY token
  echo "NO_REPLY"
else
  # Unhealthy — delivered via --announce
  echo "$output"
fi

# Always exit 0 so OpenClaw marks the run "ok" (no failure-alert spam).
# Detection is delivered through the --announce path, not the error path.
exit 0
