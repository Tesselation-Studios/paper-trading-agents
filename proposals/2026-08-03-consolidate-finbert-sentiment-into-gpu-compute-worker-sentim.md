# Proposal: Consolidate FinBERT sentiment into gpu-compute worker (sentiment job type)

**Status**: applied
**Tier**: review_required
**Created**: 2026-08-03T04:07:39.487848+00:00
**Files changed**: scripts/news_collector.py, scripts/discovery_daemon.py, requirements.txt, deploy/stonks-discovery-daemon.service

## Rationale

Raf's explicit direction 2026-08-02: 'All ML work should use the ML worker service' -- consolidate the standalone FinBERT HTTP service (port 5004, separate repo/playbook) into the gpu-compute gRPC worker (port 5002, already used for regime retraining). Adds a new sentiment job type there (openclaw-org/gpu-compute@9733a6b) and rewrites news_collector.py's score_sentiment()/score_sentiment_batch() to call it via gRPC instead of HTTP, batching all articles per collection run into one round-trip instead of one call per article. Keyword-based fallback (_compute_sentiment) unchanged for when the worker is unreachable. Plan reviewed and approved live by Raf via Claude Code plan mode before implementation.

## Evidence

news_collector.py score_sentiment()/score_sentiment_batch() rewrite (commit 838a0ca), gpu-compute worker/job_manager.py _run_sentiment handler (commit 9733a6b in ~/projects/gpu-compute), 45 passing tests in tests/test_news_collector.py including TestScoreSentimentBatchViaWorker/TestScoreSentimentBatch/TestFetchAllFeedsBatchesSentiment/TestMainBatchesAlpacaSentiment, full trader-stonks suite green (941 passed, 1 pre-existing unrelated flaky failure)

## Resolution

applied at 2026-08-03T04:07:45.152536+00:00
