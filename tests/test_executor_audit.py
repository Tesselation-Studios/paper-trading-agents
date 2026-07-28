#!/usr/bin/env python3
"""Tests for executor.py's Alpaca API audit logging (2026-07-28, Phase 4
of the local-DB migration) -- get_account/get_positions/get_open_orders/
place_order each now log every call to alpaca_audit_log via db_writer,
best-effort, without changing the original success/failure behavior.

urllib.request.urlopen is monkeypatched at the module level so the local
`import urllib.request` inside each executor.py function still resolves
to the patched version (same module object in sys.modules).

_record_alpaca_call calls db_writer.flush_all() with no db_path override
(executor.py's public functions take no db_path param -- not worth
threading one through the whole call graph just for tests). Isolation
comes from monkeypatching trader_db.DB_PATH itself so every default-path
call in this file lands on a scratch file, never the real state/trader.db."""
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import db_writer  # noqa: E402
import executor  # noqa: E402
import trader_db  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db_and_env(monkeypatch, tmp_path):
    monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
    db_writer._buffer.clear()
    monkeypatch.setenv("ALPACA_STONKS_KEY", "test-key")
    monkeypatch.setenv("ALPACA_STONKS_SECRET", "test-secret")
    yield
    db_writer._buffer.clear()


def _read_audit_rows():
    conn = trader_db.get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM alpaca_audit_log").fetchall()]
    finally:
        conn.close()


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestGetAccountAuditLogging:
    def test_success_logs_status_200(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", lambda req: _FakeResponse({"equity": "10000"}))

        result = executor.get_account("stonks")

        assert result["equity"] == "10000"
        rows = _read_audit_rows()
        assert len(rows) == 1
        assert rows[0]["endpoint"] == "account"
        assert rows[0]["status_code"] == 200

    def test_http_error_logs_status_and_reraises(self, monkeypatch):
        def raise_http_error(req):
            raise urllib.error.HTTPError("http://x", 403, "Forbidden", {}, io.BytesIO(b""))
        monkeypatch.setattr(urllib.request, "urlopen", raise_http_error)

        with pytest.raises(urllib.error.HTTPError):
            executor.get_account("stonks")

        rows = _read_audit_rows()
        assert len(rows) == 1
        assert rows[0]["status_code"] == 403

    def test_connection_error_still_logs_before_reraising(self, monkeypatch):
        def raise_url_error(req):
            raise urllib.error.URLError("connection refused")
        monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)

        with pytest.raises(urllib.error.URLError):
            executor.get_account("stonks")

        rows = _read_audit_rows()
        assert len(rows) == 1
        assert rows[0]["status_code"] is None  # URLError has no .code

    def test_audit_logging_failure_does_not_block_successful_return(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", lambda req: _FakeResponse({"equity": "10000"}))

        def raise_enqueue(*a, **kw):
            raise RuntimeError("simulated audit log failure")
        monkeypatch.setattr(executor.db_writer, "enqueue", raise_enqueue)

        result = executor.get_account("stonks")  # must not raise despite audit failure
        assert result["equity"] == "10000"


class TestPlaceOrderAuditLogging:
    def test_success_logs_request_summary(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", lambda req: _FakeResponse({"id": "abc123"}))

        result = executor.place_order("stonks", "AAPL", 1, "buy")

        assert result["id"] == "abc123"
        rows = _read_audit_rows()
        assert len(rows) == 1
        assert rows[0]["endpoint"] == "orders"
        assert rows[0]["method"] == "POST"
        assert json.loads(rows[0]["request_summary"]) == {"ticker": "AAPL", "qty": 1, "side": "buy"}

    def test_http_error_still_reraises_order_rejection(self, monkeypatch):
        def raise_http_error(req):
            raise urllib.error.HTTPError(
                "http://x", 422, "Unprocessable", {}, io.BytesIO(b'{"reason":"insufficient buying power"}'),
            )
        monkeypatch.setattr(urllib.request, "urlopen", raise_http_error)

        with pytest.raises(urllib.error.HTTPError):
            executor.place_order("stonks", "AAPL", 9999, "buy")

        rows = _read_audit_rows()
        assert rows[0]["status_code"] == 422


class TestGetOpenOrdersAuditLogging:
    def test_ticker_filter_included_in_summary(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", lambda req: _FakeResponse([]))

        result = executor.get_open_orders("stonks", ticker="AAPL")

        assert result == []
        rows = _read_audit_rows()
        assert json.loads(rows[0]["request_summary"]) == {"ticker": "AAPL"}

    def test_no_ticker_summary_is_null(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", lambda req: _FakeResponse([]))
        executor.get_open_orders("stonks")
        rows = _read_audit_rows()
        assert rows[0]["request_summary"] is None


class TestGetPositionsAuditLogging:
    def test_success_logs_endpoint(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", lambda req: _FakeResponse([]))
        executor.get_positions("stonks")
        rows = _read_audit_rows()
        assert rows[0]["endpoint"] == "positions"
        assert rows[0]["method"] == "GET"


class TestRecordAlpacaCallDirect:
    def test_enqueues_expected_row_shape(self):
        import time
        executor._record_alpaca_call("account", "GET", {"ticker": "AAA"}, 200, time.time())
        rows = _read_audit_rows()
        assert rows[0]["endpoint"] == "account"
        assert rows[0]["method"] == "GET"
        assert rows[0]["status_code"] == 200
        assert rows[0]["latency_ms"] >= 0

    def test_never_raises_even_if_db_writer_broken(self, monkeypatch):
        def raise_enqueue(*a, **kw):
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(executor.db_writer, "enqueue", raise_enqueue)
        import time
        executor._record_alpaca_call("account", "GET", None, 200, time.time())  # must not raise
