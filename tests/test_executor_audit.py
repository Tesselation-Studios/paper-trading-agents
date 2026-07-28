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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deployment_pressure  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db_and_env(monkeypatch, tmp_path):
    monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
    db_writer._buffer.clear()
    monkeypatch.setenv("ALPACA_STONKS_KEY", "test-key")
    monkeypatch.setenv("ALPACA_STONKS_SECRET", "test-secret")
    # 2026-07-28 (Phase 6c): TestBuySellPersistsPositions calls executor.main()
    # directly, which writes to several real state files as a side effect
    # (order-count/dedup bookkeeping) -- redirect all of them so a test run
    # can never pollute Stan's real daily order count or recent-orders log.
    state_dir = tmp_path / "state"
    monkeypatch.setattr(executor, "STATE_DIR", state_dir)
    monkeypatch.setattr(executor, "STOPS_STATE_PATH", state_dir / "guardrail_stops.json")
    monkeypatch.setattr(executor, "RECENT_ORDERS_PATH", state_dir / "recent_orders.json")
    monkeypatch.setattr(executor, "DAILY_ORDER_COUNT_PATH", state_dir / "daily_order_count.json")
    monkeypatch.setattr(executor, "PEAK_EQUITY_PATH", state_dir / "peak_equity.json")
    monkeypatch.setattr(executor, "EXPERIENCE_PATH", tmp_path / "experience.json")
    monkeypatch.setattr(deployment_pressure, "STATE_FILE", state_dir / "deployment_pressure.json")
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


class _FakeListResponse:
    """Same shape as _FakeResponse but for endpoints returning a JSON list
    (get_positions/get_open_orders), named separately for clarity at call
    sites even though the implementation is identical."""
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_fake_urlopen(order_response, positions_response):
    def fake_urlopen(req):
        url = req.full_url
        method = req.get_method()
        if url.endswith("/v2/orders") and method == "POST":
            return _FakeResponse(order_response)
        if url.endswith("/v2/positions"):
            return _FakeListResponse(positions_response)
        if url.endswith("/v2/account"):
            return _FakeResponse({"equity": "10000", "cash": "5000"})
        return _FakeResponse({})
    return fake_urlopen


def _run_executor(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["executor.py"] + argv)
    executor.main()


class TestBuySellPersistsPositions:
    def test_buy_creates_position_with_sector_and_thesis(self, monkeypatch, capsys):
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order1"},
            positions_response=[{"symbol": "AAA", "qty": "3", "avg_entry_price": "10.00", "market_value": "30.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "3",
            "--price", "10.00", "--sector", "Technology", "--thesis", "momentum entry",
            "--skip-guardrails",
        ])
        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["status"] == "open"
        assert row["shares"] == 3.0
        assert row["sector"] == "Technology"
        assert row["thesis"] == "momentum entry"

    def test_buy_shares_come_from_live_alpaca_not_local_arithmetic(self, monkeypatch, capsys):
        """Scale-in: Alpaca's post-fill qty (5) is the source of truth for
        total shares, not args.qty (2, just the incremental buy)."""
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order2"},
            positions_response=[{"symbol": "AAA", "qty": "5", "avg_entry_price": "10.50", "market_value": "52.50"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "2",
            "--price", "11.00", "--skip-guardrails",
        ])
        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["shares"] == 5.0

    def test_buy_without_thesis_warns_but_does_not_fail(self, monkeypatch, capsys):
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order3"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "10.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "1",
            "--price", "10.00", "--skip-guardrails",
        ])
        err = capsys.readouterr().err
        assert "no --thesis provided" in err
        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["thesis"] is None

    def test_sell_full_exit_closes_position(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=2.0, entry_price=10.0, entry_time="t1")
        conn.close()

        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order4"},
            positions_response=[{"symbol": "AAA", "qty": "2", "avg_entry_price": "10.00", "market_value": "24.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "AAA", "--qty", "2",
            "--price", "12.00", "--close-reason", "trailing stop breach", "--skip-guardrails",
        ])

        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["status"] == "closed"
        assert row["close_reason"] == "trailing stop breach"
        assert row["realized_pnl"] == pytest.approx(4.0)

    def test_sell_partial_trim_keeps_position_open_with_reduced_shares(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=5.0, entry_price=10.0, entry_time="t1", sector="Tech", thesis="original")
        conn.close()

        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order5"},
            positions_response=[{"symbol": "AAA", "qty": "5", "avg_entry_price": "10.00", "market_value": "60.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "AAA", "--qty", "2",
            "--price", "12.00", "--skip-guardrails",
        ])

        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["status"] == "open"
        assert row["shares"] == 3.0
        # sector/thesis preserved -- upsert_position's ON CONFLICT COALESCEs them
        assert row["sector"] == "Tech"
        assert row["thesis"] == "original"

    def test_sell_default_close_reason_when_omitted(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.close()

        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order6"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "10.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "AAA", "--qty", "1",
            "--price", "9.00", "--skip-guardrails",
        ])

        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["close_reason"] == "manual SELL"

    def test_positions_write_failure_does_not_block_trade(self, monkeypatch, capsys):
        """Best-effort: the order already executed by the time this runs.
        Mocks upsert_position specifically, not get_conn broadly -- bankroll.py's
        own read_bankroll()/write_bankroll() calls in this same BUY block use
        the same trader_db.get_conn() and have no try/except of their own
        (pre-existing, out of scope for this phase); mocking get_conn itself
        would incorrectly exercise that gap instead of the thing this test
        is actually about."""
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order7"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "10.00"}],
        ))

        def raise_upsert_position(*a, **kw):
            raise RuntimeError("simulated DB failure")
        monkeypatch.setattr(executor.trader_db, "upsert_position", raise_upsert_position)

        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "1",
            "--price", "10.00", "--skip-guardrails",
        ])  # must not raise

        err = capsys.readouterr().err
        assert "positions table write failed" in err


class TestSectorGateReadsDb:
    def test_sector_of_reads_positions_table(self, monkeypatch):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1", sector="Financials")
        conn.close()
        assert executor._sector_of("aaa") == "Financials"

    def test_sector_of_unknown_ticker_returns_none(self, monkeypatch):
        assert executor._sector_of("ZZZ") is None

    def test_sector_of_db_failure_returns_none_not_raise(self, monkeypatch):
        def raise_get_conn(db_path=None):
            raise RuntimeError("simulated DB failure")
        monkeypatch.setattr(executor.trader_db, "get_conn", raise_get_conn)
        assert executor._sector_of("AAA") is None


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
