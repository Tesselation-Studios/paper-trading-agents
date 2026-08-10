# Vendored 2026-08-10 from ~/projects/gpu-compute/generated/ -- only
# gpu_compute_pb2.py (protoc-generated message/enum definitions, no live
# service code) is copied here, deliberately not gpu_compute_pb2_grpc.py
# or orchestrator/gpu_client.py (the real gRPC client, which carries real
# staleness/drift risk if vendored -- see tasks/pending.md history).
# scripts/news_collector.py's real _score_sentiment_batch_via_worker()
# still imports the live orchestrator.gpu_client from GPU_COMPUTE_ROOT at
# call time, unaffected by this package -- this exists only so
# `from generated import gpu_compute_pb2` resolves in environments (CI)
# that don't have the sibling repo, for the tests that only need the
# message definitions, not a live worker connection.
