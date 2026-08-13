#!/bin/bash
# Cron wrapper: gates on --check-health exit code so healthy runs are silent
# (NO_REPLY token) and unhealthy runs deliver a readable single-line summary.
#
# NOTE: do NOT use `IFS=$'\n' read -r a b c d <<< "$multiline"` to split the
# JSON fields — `read` only consumes one line, so completed/sentiment/downstream
# end up empty. Let Python emit the full formatted line instead.
set -uo pipefail
cd /home/openclaw/.openclaw/workspace-trader-stonks

output=$(python3 scripts/discovery_daemon.py --check-health 2>&1)
rc=$?

if [ "$rc" -eq 0 ]; then
  # Healthy — suppressed by OpenClaw's NO_REPLY token
  echo "NO_REPLY"
else
  # Unhealthy — Python emits ONE formatted line; bash just relays it.
  summary=$(echo "$output" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    status = d.get('last_cycle_status', '?')
    completed = d.get('last_cycle_completed_at', '?')
    sentiment = d.get('sentiment_worker_ok', '?')
    flow = d.get('downstream_flow')
    if flow is None:
        downstream = 'n/a (off-hours or nothing fresh to promote)'
    elif flow.get('ok'):
        downstream = 'ok'
    else:
        downstream = f\"STALLED -- {flow.get('fresh_in_band_count')} fresh in-band candidates but last watchlist add was {flow.get('elapsed_seconds')}s ago (last: {flow.get('last_watchlist_touch')})\"
    print(f'⚠️ Discovery daemon health check — last cycle: {status} at {completed}, sentiment worker: {sentiment}, downstream flow: {downstream}')
except Exception as e:
    print('⚠️ Discovery daemon health check failed to parse JSON: ' + repr(e))
" 2>&1)
  if [ $? -eq 0 ] && [ -n "$summary" ]; then
    echo "$summary"
  else
    echo "⚠️ Discovery daemon health check failed: $output"
  fi
fi

# Always exit 0 so OpenClaw marks the run "ok" (no failure-alert spam).
# Detection is delivered through readable output, not the error path.
exit 0
