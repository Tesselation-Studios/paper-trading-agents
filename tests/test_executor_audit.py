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
import alpaca_client  # noqa: E402
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
    monkeypatch.setattr(alpaca_client, "STATE_DIR", state_dir)
    monkeypatch.setattr(executor, "STOPS_STATE_PATH", state_dir / "guardrail_stops.json")
    monkeypatch.setattr(executor, "RECENT_ORDERS_PATH", state_dir / "recent_orders.json")
    monkeypatch.setattr(alpaca_client, "RECENT_ORDERS_PATH", state_dir / "recent_orders.json")
    monkeypatch.setattr(executor, "DAILY_ORDER_COUNT_PATH", state_dir / "daily_order_count.json")
    monkeypatch.setattr(alpaca_client, "DAILY_ORDER_COUNT_PATH", state_dir / "daily_order_count.json")
    monkeypatch.setattr(executor, "PEAK_EQUITY_PATH", state_dir / "peak_equity.json")
    monkeypatch.setattr(alpaca_client, "PEAK_EQUITY_PATH", state_dir / "peak_equity.json")
    monkeypatch.setattr(executor, "EXPERIENCE_PATH", tmp_path / "experience.json")
    monkeypatch.setattr(alpaca_client, "EXPERIENCE_PATH", tmp_path / "experience.json")
    monkeypatch.setattr(deployment_pressure, "STATE_FILE", state_dir / "deployment_pressure.json")
    yield
    db_writer._buffer.clear()


def _read_audit_rows():
    conn = trader_db.get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM alpaca_audit_log").fetchall()]
    finally:
        conn.close()


def _read_decisions_rows():
    conn = trader_db.get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()]
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


class TestDecisionLogging:
    """2026-08-02: decisions-table row is now written directly from
    executor.py's BUY/SELL paths (_record_decision_row), not only via the
    standalone record_decision.py CLI -- see that file's updated docstring
    and _load_experience()'s docstring in this module for the root cause
    this mirrors."""

    def test_buy_writes_decisions_row(self, monkeypatch, capsys):
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order10"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "10.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "1",
            "--price", "10.00", "--conviction", "0.72", "--thesis", "momentum entry",
            "--skip-guardrails",
        ])
        rows = _read_decisions_rows()
        assert len(rows) == 1
        assert rows[0]["ticker"] == "AAA"
        assert rows[0]["decision"] == "BUY"
        assert rows[0]["conviction"] == pytest.approx(0.72)
        assert rows[0]["rationale"] == "momentum entry"

    def test_sell_writes_decisions_row_with_close_reason_as_rationale(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.close()

        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order11"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "12.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "AAA", "--qty", "1",
            "--price", "12.00", "--conviction", "0.5", "--close-reason", "trailing stop breach",
            "--skip-guardrails",
        ])
        rows = _read_decisions_rows()
        assert len(rows) == 1
        assert rows[0]["decision"] == "SELL"
        assert rows[0]["rationale"] == "trailing stop breach"

    def test_buy_decisions_row_features_match_training_example_features(self, monkeypatch, capsys):
        """Regression guard against the decisions row and the training_examples
        row (written by the pre-existing record_entry_example mechanization)
        drifting apart -- both should reflect the same scored signal."""
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order12"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "10.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "1",
            "--price", "10.00", "--conviction", "0.6",
            "--features", '{"technical": {"direction": "bullish", "confidence": 0.6}}',
            "--skip-guardrails",
        ])
        decisions_row = _read_decisions_rows()[0]
        decision_features = json.loads(decisions_row["decision_json"])["features"]
        assert decision_features["technical"]["confidence"] == pytest.approx(0.6)

        conn = trader_db.get_conn()
        try:
            te_row = conn.execute("SELECT * FROM training_examples WHERE ticker = ?", ("AAA",)).fetchone()
        finally:
            conn.close()
        te_features = json.loads(te_row["features"])
        assert te_features["technical"]["confidence"] == pytest.approx(0.6)

    def test_decision_logging_failure_does_not_block_trade(self, monkeypatch, capsys):
        """Not optional -- the single highest-risk regression this change
        could introduce is a working trade suddenly failing because of a
        bookkeeping bug. Same fail-open discipline as
        test_positions_write_failure_does_not_block_trade above."""
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order13"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "10.00"}],
        ))

        import decisions as decisions_module
        def raise_record_decision(*a, **kw):
            raise RuntimeError("simulated decision-log failure")
        monkeypatch.setattr(decisions_module, "record_decision", raise_record_decision)

        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "1",
            "--price", "10.00", "--skip-guardrails",
        ])  # must not raise

        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["status"] == "open"  # the trade itself still went through

        err = capsys.readouterr().err
        assert "decision log failed" in err


class TestSellEntryPriceFallback:
    """2026-08-02: SELL-path entry_price lookup falls back to the local
    trader_db positions row when the live Alpaca get_positions() scan
    misses the ticker (stale broker sync, a race, already-flat) -- see
    executor.py's inline comments for the full incident history (27 of 61
    total_trades sitting 'unclassified')."""

    def test_falls_back_to_local_position_when_missing_from_live(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=2.0, entry_price=10.0, entry_time="t1")
        conn.close()

        # Live Alpaca positions list does NOT include AAA -- simulates the
        # stale-lookup case. place_order's own response still succeeds.
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order20"},
            positions_response=[],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "AAA", "--qty", "2",
            "--price", "12.00", "--skip-guardrails",
        ])

        err = capsys.readouterr().err
        assert "entry_price unavailable" not in err

        rows = _read_decisions_rows()
        assert len(rows) == 1
        assert rows[0]["decision"] == "SELL"  # close_trade_outcome ran -> decision row got written

        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["status"] == "closed"
        # entry_price=10.0 (local fallback), exit_price=12.00 (--price) -> pnl = 2.0 * 2 shares
        assert row["realized_pnl"] == pytest.approx(4.0)

    def test_unavailable_both_sources_logs_warning_and_skips_outcome(self, monkeypatch, capsys):
        # No local position row seeded, and live Alpaca positions also empty.
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order21"},
            positions_response=[],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "ZZZ", "--qty", "1",
            "--price", "12.00", "--skip-guardrails",
        ])

        err = capsys.readouterr().err
        assert "entry_price unavailable for SELL ZZZ" in err
        assert "missing from both live Alpaca positions and local trader_db" in err

        # Outcome bookkeeping skipped entirely -- no decisions row, no
        # bankroll/experience update -- silent skip must become a LOUD
        # skip, not become a forced (fabricated) success.
        assert _read_decisions_rows() == []

    def test_prefers_live_over_local_when_both_present(self, monkeypatch, capsys):
        """Live lookup is unchanged/preferred when it succeeds -- this
        fallback only fires when live is missing."""
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=2.0, entry_price=10.0, entry_time="t1")
        conn.close()

        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order22"},
            positions_response=[{"symbol": "AAA", "qty": "2", "avg_entry_price": "12.00", "market_value": "24.00"}],
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
        # entry_price=12.0 (LIVE, not the local row's 10.0), exit_price=12.00 -> pnl = 0
        assert row["realized_pnl"] == pytest.approx(0.0)


class TestSellExitPriceFallback:
    """2026-08-03: the SELL path had no real-fill lookup, so omitting
    --price silently collapsed exit_price to entry_price -> $0.00 pnl,
    mislabeled as a LOSS (live incident: ZBRA/DXCM/OOMA bootstrap
    quick-exits). Fixed to mirror the BUY path's wait_for_fill() lookup."""

    def _fake_urlopen(self, order_response, positions_response, fill_response):
        def fake_urlopen(req):
            url = req.full_url
            method = req.get_method()
            if url.endswith("/v2/orders") and method == "POST":
                return _FakeResponse(order_response)
            if "/v2/orders/" in url and method == "GET":
                return _FakeResponse(fill_response)
            if url.endswith("/v2/positions"):
                return _FakeListResponse(positions_response)
            if url.endswith("/v2/account"):
                return _FakeResponse({"equity": "10000", "cash": "5000"})
            return _FakeResponse({})
        return fake_urlopen

    def test_sell_uses_a_longer_timeout_than_the_buy_path_default(self, monkeypatch, capsys):
        """2026-08-10: a $0.00 realized_pnl here doesn't just mean a stale
        training-example feature (the BUY path's concern) -- it silently
        and permanently corrupts the trade's record, with no after-the-fact
        reconciliation for closed trades. Confirmed live: VSXY's real
        +10.80% exit recorded $0.00/0.00% this way, plausibly because
        wait_for_fill()'s shared 1.0s default didn't give the paper fill
        enough time to register. Locks in that the SELL path now asks for
        more time instead of silently reproducing that gap."""
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=2.0, entry_price=10.0, entry_time="t1")
        conn.close()

        calls = []
        real_wait_for_fill = executor.wait_for_fill

        def spy_wait_for_fill(account, order_id, *args, **kwargs):
            calls.append(kwargs.get("timeout", args[0] if args else None))
            return real_wait_for_fill(account, order_id, *args, **kwargs)
        monkeypatch.setattr(executor, "wait_for_fill", spy_wait_for_fill)

        monkeypatch.setattr(urllib.request, "urlopen", self._fake_urlopen(
            order_response={"id": "order33"},
            positions_response=[{"symbol": "AAA", "qty": "2", "avg_entry_price": "10.00", "market_value": "22.74"}],
            fill_response={"id": "order33", "status": "filled", "filled_avg_price": "11.37"},
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "AAA", "--qty", "2",
            "--close-reason", "bootstrap quick-exit", "--skip-guardrails",
        ])
        assert calls == [5.0]

    def test_sell_without_price_uses_real_fill_price(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=2.0, entry_price=10.0, entry_time="t1")
        conn.close()

        monkeypatch.setattr(urllib.request, "urlopen", self._fake_urlopen(
            order_response={"id": "order30"},
            positions_response=[{"symbol": "AAA", "qty": "2", "avg_entry_price": "10.00", "market_value": "22.74"}],
            fill_response={"id": "order30", "status": "filled", "filled_avg_price": "11.37"},
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "AAA", "--qty", "2",
            "--close-reason", "bootstrap quick-exit", "--skip-guardrails",
        ])

        err = capsys.readouterr().err
        assert "exit_price unavailable" not in err

        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["status"] == "closed"
        # entry=10.0, real fill=11.37 -> pnl = 1.37 * 2, NOT $0.00
        assert row["realized_pnl"] == pytest.approx(2.74)

    def test_sell_with_price_still_takes_priority_over_fill_price(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=2.0, entry_price=10.0, entry_time="t1")
        conn.close()

        # A different fill price (11.37) is mocked, but an explicit --price
        # must still win -- preserves every existing caller's behavior.
        monkeypatch.setattr(urllib.request, "urlopen", self._fake_urlopen(
            order_response={"id": "order31"},
            positions_response=[{"symbol": "AAA", "qty": "2", "avg_entry_price": "10.00", "market_value": "24.00"}],
            fill_response={"id": "order31", "status": "filled", "filled_avg_price": "11.37"},
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
        # entry=10.0, --price=12.00 wins over the mocked 11.37 fill -> pnl = 2.0 * 2
        assert row["realized_pnl"] == pytest.approx(4.0)

    def test_sell_falls_back_to_entry_price_and_warns_when_no_price_and_no_fill(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        conn.close()

        # fill_response has no filled_avg_price (order never filled within
        # wait_for_fill's timeout) -- true last-resort fallback path.
        monkeypatch.setattr(urllib.request, "urlopen", self._fake_urlopen(
            order_response={"id": "order32"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "10.00"}],
            fill_response={"id": "order32", "status": "new"},
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "AAA", "--qty", "1",
            "--close-reason", "bootstrap quick-exit", "--skip-guardrails",
        ])

        err = capsys.readouterr().err
        assert "exit_price unavailable for SELL AAA" in err
        assert "ZERO_PNL_ANOMALY" in err

        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["realized_pnl"] == pytest.approx(0.0)

    def test_sell_with_real_pnl_does_not_warn_zero_pnl_anomaly(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=2.0, entry_price=10.0, entry_time="t1")
        conn.close()

        monkeypatch.setattr(urllib.request, "urlopen", self._fake_urlopen(
            order_response={"id": "order34"},
            positions_response=[{"symbol": "AAA", "qty": "2", "avg_entry_price": "10.00", "market_value": "22.74"}],
            fill_response={"id": "order34", "status": "filled", "filled_avg_price": "11.37"},
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "AAA", "--qty", "2",
            "--close-reason", "bootstrap quick-exit", "--skip-guardrails",
        ])

        assert "ZERO_PNL_ANOMALY" not in capsys.readouterr().err


class TestLongPlayCli:
    """End-to-end CLI coverage for --play-type long (2026-07-30,
    params.json risk.long_play) -- the validation in main() (both
    --predicted-by-date and --prediction-reason required together, valid
    date format) and that the fields actually land in the positions row."""

    def test_long_play_buy_persists_all_fields(self, monkeypatch, capsys):
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order-lp1"},
            positions_response=[{"symbol": "BVS", "qty": "5", "avg_entry_price": "10.00", "market_value": "50.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "BVS", "--qty", "5",
            "--price", "10.00", "--thesis", "earnings beat expected",
            "--play-type", "long", "--predicted-by-date", "2026-08-02",
            "--prediction-reason", "earnings beat expected",
            "--thesis-invalidation", "guidance cut on the call", "--skip-guardrails",
        ])
        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "BVS")
        finally:
            conn.close()
        assert row["play_type"] == "long"
        assert row["predicted_by_date"] == "2026-08-02"
        assert row["prediction_reason"] == "earnings beat expected"
        assert row["thesis_invalidation"] == "guidance cut on the call"

    def test_long_play_missing_predicted_by_date_rejected(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_executor(monkeypatch, [
                "--account", "stonks", "--action", "BUY", "--ticker", "BVS", "--qty", "5",
                "--price", "10.00", "--play-type", "long",
                "--prediction-reason", "earnings beat expected", "--skip-guardrails",
            ])
        err = capsys.readouterr().out
        assert "requires both --predicted-by-date and --prediction-reason" in err

    def test_long_play_missing_prediction_reason_rejected(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_executor(monkeypatch, [
                "--account", "stonks", "--action", "BUY", "--ticker", "BVS", "--qty", "5",
                "--price", "10.00", "--play-type", "long",
                "--predicted-by-date", "2026-08-02", "--skip-guardrails",
            ])
        err = capsys.readouterr().out
        assert "requires both --predicted-by-date and --prediction-reason" in err

    def test_long_play_bad_date_format_rejected(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_executor(monkeypatch, [
                "--account", "stonks", "--action", "BUY", "--ticker", "BVS", "--qty", "5",
                "--price", "10.00", "--play-type", "long",
                "--predicted-by-date", "08/02/2026", "--prediction-reason", "x", "--skip-guardrails",
            ])
        err = capsys.readouterr().out
        assert "must be YYYY-MM-DD" in err

    def test_long_play_on_sell_rejected(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="BVS", shares=5.0, entry_price=10.0, entry_time="t1")
        conn.close()
        with pytest.raises(SystemExit):
            _run_executor(monkeypatch, [
                "--account", "stonks", "--action", "SELL", "--ticker", "BVS", "--qty", "5",
                "--price", "10.00", "--play-type", "long",
                "--predicted-by-date", "2026-08-02", "--prediction-reason", "x", "--skip-guardrails",
            ])
        err = capsys.readouterr().out
        assert "--play-type long only valid for BUY" in err

    def test_standard_buy_unaffected_by_long_play_validation(self, monkeypatch, capsys):
        """A plain BUY (play_type defaults to 'standard') must not require
        --predicted-by-date/--prediction-reason at all."""
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order-lp2"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "10.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "1",
            "--price", "10.00", "--thesis", "momentum entry", "--skip-guardrails",
        ])  # must not raise
        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "AAA")
        finally:
            conn.close()
        assert row["play_type"] == "standard"


class TestThesisPersistenceCli:
    """2026-08-03: --thesis-claim/--thesis-invalidation on BUY. Hard-required
    for --play-type long/conviction (alongside the existing --prediction-reason
    requirement); soft-required (warn-only) for standard, matching --thesis's
    existing precedent."""

    def test_conviction_play_missing_thesis_invalidation_rejected(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_executor(monkeypatch, [
                "--account", "stonks", "--action", "BUY", "--ticker", "BVS", "--qty", "5",
                "--price", "10.00", "--play-type", "conviction",
                "--prediction-reason", "strong earnings momentum", "--skip-guardrails",
            ])
        err = capsys.readouterr().out
        assert "--play-type conviction requires --thesis-invalidation" in err

    def test_long_play_missing_thesis_invalidation_rejected(self, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_executor(monkeypatch, [
                "--account", "stonks", "--action", "BUY", "--ticker", "BVS", "--qty", "5",
                "--price", "10.00", "--play-type", "long",
                "--predicted-by-date", "2026-08-02", "--prediction-reason", "earnings beat expected",
                "--skip-guardrails",
            ])
        err = capsys.readouterr().out
        assert "--play-type long requires --thesis-invalidation" in err

    def test_conviction_play_buy_persists_thesis_fields(self, monkeypatch, capsys):
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order-cv1"},
            positions_response=[{"symbol": "BVS", "qty": "10", "avg_entry_price": "10.00", "market_value": "100.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "BVS", "--qty", "10",
            "--price", "10.00", "--thesis", "strong momentum", "--play-type", "conviction",
            "--prediction-reason", "strong earnings momentum",
            "--thesis-claim", "BVS re-rates up over 3 weeks on margin expansion",
            "--thesis-invalidation", "closes below 20-day MA on rising volume",
            "--features", '{"technical": {"direction": "bullish", "confidence": 0.8}}',
            "--skip-guardrails",
        ])
        conn = trader_db.get_conn()
        try:
            row = trader_db.get_position(conn, "BVS")
            log = trader_db.get_thesis_log(conn, "BVS")
        finally:
            conn.close()
        assert row["thesis_claim"] == "BVS re-rates up over 3 weeks on margin expansion"
        assert row["thesis_invalidation"] == "closes below 20-day MA on rising volume"
        assert row["thesis_entry_signals"] == '{"technical": {"direction": "bullish", "confidence": 0.8}}'
        assert len(log) == 1
        assert log[0]["event_type"] == "entry"
        assert log[0]["claim"] == "BVS re-rates up over 3 weeks on margin expansion"

    def test_standard_buy_thesis_invalidation_optional(self, monkeypatch, capsys):
        """A plain BUY must not require --thesis-claim/--thesis-invalidation
        at all -- only a stderr warning, matching --thesis's own precedent."""
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order-std1"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "10.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "1",
            "--price", "10.00", "--thesis", "momentum entry", "--skip-guardrails",
        ])  # must not raise
        err = capsys.readouterr().err
        assert "no --thesis-claim/--thesis-invalidation" in err

    def test_scale_in_logs_scale_in_event_not_entry(self, monkeypatch, capsys):
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t0")
        conn.close()
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order-si1"},
            positions_response=[{"symbol": "AAA", "qty": "2", "avg_entry_price": "10.00", "market_value": "20.00"}],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "1",
            "--price", "10.00", "--thesis-claim", "adding on strength",
            "--thesis-invalidation", "closes below 9.50", "--skip-guardrails",
        ])
        conn = trader_db.get_conn()
        try:
            log = trader_db.get_thesis_log(conn, "AAA")
        finally:
            conn.close()
        assert log[0]["event_type"] == "scale_in"

    def test_thesis_detail_write_failure_does_not_block_trade(self, monkeypatch, capsys):
        """Fail-open: a broken log_thesis_event must never turn a
        successful fill into a failed order."""
        monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(
            order_response={"id": "order-fo1"},
            positions_response=[{"symbol": "AAA", "qty": "1", "avg_entry_price": "10.00", "market_value": "10.00"}],
        ))
        monkeypatch.setattr(trader_db, "log_thesis_event",
                             lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated DB failure")))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "1",
            "--price", "10.00", "--thesis-claim", "x", "--thesis-invalidation", "y",
            "--skip-guardrails",
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


class TestProtectiveStopCliFlow:
    """End-to-end ordering of the 2026-08-01 broker-side protective stop.

    The SELL half is a correctness requirement, not tidiness: a resting GTC
    sell stop reserves the shares it covers, so Alpaca rejects a market SELL
    of the same position for insufficient quantity while that order is still
    on the book. The cancel has to happen BEFORE the sell is submitted.
    """

    def _fake_urlopen(self, calls, positions, open_orders):
        def fake_urlopen(req):
            url = req.full_url
            method = req.get_method()
            if "/v2/orders" in url and method == "POST":
                body = json.loads(req.data.decode())
                calls.append(("POST", body.get("type"), body.get("side"), body.get("stop_price")))
                return _FakeResponse({"id": "order-1", "status": "filled",
                                       "filled_avg_price": "10.00", "filled_qty": "5"})
            if "/v2/orders/" in url and method == "DELETE":
                calls.append(("DELETE", url.rsplit("/", 1)[-1]))
                open_orders.clear()
                return _FakeResponse({})
            if "/v2/orders/" in url and method == "GET":
                # single-order lookup returns an object, not a list
                calls.append(("GET", "order"))
                return _FakeResponse({"id": "order-1", "status": "filled",
                                       "filled_avg_price": "10.00", "filled_qty": "5"})
            if "/v2/orders" in url and method == "GET":
                calls.append(("GET", "orders"))
                return _FakeListResponse(list(open_orders))
            if url.endswith("/v2/positions"):
                return _FakeListResponse(positions)
            if url.endswith("/v2/account"):
                return _FakeResponse({"equity": "10000", "cash": "5000"})
            return _FakeResponse({})
        return fake_urlopen

    def test_buy_places_protective_stop_after_fill(self, monkeypatch, capsys):
        calls = []
        monkeypatch.setattr(urllib.request, "urlopen", self._fake_urlopen(
            calls,
            positions=[{"symbol": "AAA", "qty": "5", "avg_entry_price": "10.00",
                         "current_price": "10.20", "market_value": "51.00"}],
            open_orders=[],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "5",
            "--price", "10.00", "--thesis", "momentum entry", "--skip-guardrails",
        ])
        stop_posts = [c for c in calls if c[0] == "POST" and c[1] == "stop"]
        assert len(stop_posts) == 1
        assert stop_posts[0][2] == "sell"
        # params.json risk.stop_loss_pct (-6%, v1.20) below the $10.00 entry.
        assert float(stop_posts[0][3]) == pytest.approx(9.39)

    def test_sell_cancels_resting_stop_before_submitting(self, monkeypatch, capsys):
        calls = []
        open_orders = [{"id": "stop-1", "symbol": "AAA", "side": "sell", "type": "stop",
                         "stop_price": "9.00", "qty": "5"}]
        monkeypatch.setattr(urllib.request, "urlopen", self._fake_urlopen(
            calls,
            positions=[{"symbol": "AAA", "qty": "5", "avg_entry_price": "10.00",
                         "current_price": "11.00", "market_value": "55.00"}],
            open_orders=open_orders,
        ))
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=5.0, entry_price=10.0, entry_time="t1")
        conn.close()

        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "SELL", "--ticker", "AAA", "--qty", "5",
            "--price", "11.00", "--close-reason", "profit target", "--skip-guardrails",
        ])
        kinds = [c[0] for c in calls]
        assert "DELETE" in kinds, calls
        assert kinds.index("DELETE") < kinds.index("POST"), calls

    def test_buy_scale_in_cancels_resting_stop_before_submitting(self, monkeypatch, capsys):
        """2026-08-05: a BUY scale-in on a position that already carries a
        resting protective stop was 403ing at Alpaca all session (confirmed
        live -- BFST/BBSI). Same requirement as the SELL half above, mirrored:
        cancel the resting stop before submitting the new BUY, then
        ensure_protective_stop() (already unconditional on the BUY path)
        re-places it sized to the post-fill position.
        """
        calls = []
        open_orders = [{"id": "stop-1", "symbol": "AAA", "side": "sell", "type": "stop",
                         "stop_price": "9.00", "qty": "5"}]
        monkeypatch.setattr(urllib.request, "urlopen", self._fake_urlopen(
            calls,
            positions=[{"symbol": "AAA", "qty": "7", "avg_entry_price": "10.00",
                         "current_price": "10.20", "market_value": "71.40"}],
            open_orders=open_orders,
        ))
        conn = trader_db.get_conn()
        trader_db.upsert_position(conn, ticker="AAA", shares=5.0, entry_price=10.0, entry_time="t1")
        conn.close()

        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "2",
            "--price", "10.00", "--thesis", "scale-in", "--skip-guardrails",
        ])
        kinds = [c[0] for c in calls]
        assert "DELETE" in kinds, calls
        buy_post_idx = next(i for i, c in enumerate(calls) if c[0] == "POST" and c[2] == "buy")
        assert kinds.index("DELETE") < buy_post_idx, calls
        # the floor comes back afterward, sized to the whole post-fill position
        stop_posts = [c for c in calls if c[0] == "POST" and c[1] == "stop"]
        assert len(stop_posts) == 1
        assert stop_posts[0][2] == "sell"

    def test_buy_new_position_skips_cancel_when_no_resting_stop(self, monkeypatch, capsys):
        """A first-time BUY (no existing position, no resting stop) shouldn't
        attempt a cancel at all -- nothing to cancel, and no extra DELETE
        round-trip for the common case."""
        calls = []
        monkeypatch.setattr(urllib.request, "urlopen", self._fake_urlopen(
            calls,
            positions=[{"symbol": "AAA", "qty": "2", "avg_entry_price": "10.00",
                         "current_price": "10.20", "market_value": "20.40"}],
            open_orders=[],
        ))
        _run_executor(monkeypatch, [
            "--account", "stonks", "--action", "BUY", "--ticker", "AAA", "--qty", "2",
            "--price", "10.00", "--thesis", "new entry", "--skip-guardrails",
        ])
        kinds = [c[0] for c in calls]
        assert "DELETE" not in kinds, calls
