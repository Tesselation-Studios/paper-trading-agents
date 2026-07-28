#!/bin/bash
# Cron wrapper: lightweight enqueue-only. Creates a stonks-schedule card
# instead of running a full agentTurn — no LLM call, near-zero event-loop
# footprint. Actual work (news_collector.py) happens in stonks-queue-drain.
set -uo pipefail
cd /home/openclaw/.openclaw/workspace-trader-stonks

/home/openclaw/.npm-global/bin/openclaw workboard create \
  "[stonks-schedule] sentiment-refresh $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --board stonks-schedule \
  --agent trader-stonks \
  --priority high \
  --labels type:sentiment-refresh \
  --notes "Run: python3 scripts/news_collector.py (no args, defaults to load_live_universe() = open positions + watchlist). Writes state/sentiment_cache.json via Alpaca News -> FinBERT. Do NOT trade, do NOT call executor.py. Fails open on Alpaca/FinBERT errors — no escalation on single failure." \
  --json

echo "NO_REPLY"
exit 0
