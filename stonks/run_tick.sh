#!/usr/bin/env bash
# run_tick.sh — Run one Stonks tick with 3 retries on timeout.
# Called by cron. If the tick fails (timeout/error), waits 45s and retries.
set -euo pipefail

TICK_LABEL="stonks-tick"
LOCKFILE="/tmp/${TICK_LABEL}.lock"
MAX_RETRIES=3
RETRY_DELAY=45  # seconds

# ── Acquire lock ──────────────────────────────────────────────────────────
exec 200>"$LOCKFILE"
flock -n 200 || {
    echo "[$(date)] Previous tick still running. Skipping."
    exit 0
}

# ── Bankroll refresh ──────────────────────────────────────────────────────
BANKROLL_PY="/home/openclaw/.openclaw/workspace-trader-stonks/bankroll.py"
python3 "$BANKROLL_PY" 2>/dev/null || true

# ── Tick function ─────────────────────────────────────────────────────────
run_tick() {
    local attempt=$1
    local logfile="/home/openclaw/projects/paper-trading-rebuild/logs/stonks-ticks/$(date +%Y%m%d-%H%M)-attempt-${attempt}.log"
    mkdir -p "$(dirname "$logfile")"

    echo "[$(date)] Tick attempt $attempt/$MAX_RETRIES" | tee -a "$logfile"

    # Use OpenClaw CLI to trigger the tick agent
    # Falls back to a direct data-bus analysis if the agent times out
    if openclaw cron run "$TICK_LABEL" --run-mode force 2>>"$logfile"; then
        echo "[$(date)] ✓ Tick $attempt succeeded" | tee -a "$logfile"
        return 0
    else
        local exit_code=$?
        echo "[$(date)] ✗ Tick $attempt failed (exit $exit_code)" | tee -a "$logfile"
        return $exit_code
    fi
}

# ── Main retry loop ───────────────────────────────────────────────────────
for attempt in $(seq 1 $MAX_RETRIES); do
    if run_tick "$attempt"; then
        exit 0
    fi
    if [[ $attempt -lt $MAX_RETRIES ]]; then
        echo "[$(date)] Waiting ${RETRY_DELAY}s before retry $((attempt + 1))..."
        sleep $RETRY_DELAY
    fi
done

echo "[$(date)] All $MAX_RETRIES attempts failed." >&2
exit 1