#!/usr/bin/env python3
"""
Unit tests for scripts/replay_order.py -- the simulation-only order/gate
path for the historical-replay harness (2026-08-01). Real sqlite3 files
under tmp_path via backtest_session.py's own create_session(), no Alpaca
mocking needed since replay_order.py never talks to Alpaca at all.

Three bugs were found and fixed by hand-testing this script before these
tests were written -- each has a dedicated regression test:
  1. gate_hours used the real wall clock, not the simulated date, so a
     backtest session could never trade except during real market hours.
  2. gate_drawdown_circuit_breaker (and 3 other gates) read/wrote fixed-
     path LIVE state files, not the context dict -- confirmed writing to
     the real state/peak_equity.json during manual testing.
  3. A scale-in's weighted-average entry price never persisted (upsert_
     position's ON CONFLICT clause doesn't update entry_price), so a
     later SELL's pnl/return_pct used the stale original entry price.
  4. Calling record_decision() for a SELL before record_trade_close()
     re-triggered the exact mislabeling bug fixed elsewhere this morning
     -- find_entry_training_example()'s exact-position-link tier doesn't
     filter by example_type, so the SELL's own row won the "newest
     unlabeled row with this position_entry_time" race.
"""
import datetime
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import backtest_session as bs  # noqa: E402
import executor  # noqa: E402
import replay_order  # noqa: E402
import trader_db  # noqa: E402

DEFAULT_GUARDRAIL_GATES = {
    "cash": True, "position_size": True, "long_play": True, "conviction_play": True,
    "max_portfolio_risk": True, "max_positions": False,
    "sector_concentration": True, "hours": True, "conviction": True,
    "bankroll": True, "duplicate_order": True, "order_idempotency": True,
    "order_count_audit": True, "drawdown_circuit_breaker": True,
}
DEFAULT_PARAMS = {
    "risk": {"max_position_pct": 6.0, "conviction_floor": 0.10,
              "max_portfolio_risk_pct": 8.0, "drawdown_pause_pct": 15.0, "drawdown_halt_pct": 20.0,
              "long_play": {"enabled": True, "position_size_pct": 3.0, "max_concurrent_long_plays": 2},
              "conviction_play": {"enabled": True, "position_size_pct": 10.0,
                                   "max_concurrent_conviction_plays": 5, "trail_multiplier": 1.5}},
    "risk_guards": {"max_positions_per_sector": 5},
    "guardrail_gates": dict(DEFAULT_GUARDRAIL_GATES),
}

# A Wednesday, so gate_hours' real weekday check can't accidentally agree
# with the simulated one and mask a bug either way.
SIM_TS = "2026-06-03T10:30:00-04:00"


@pytest.fixture
def session_env(tmp_path, monkeypatch):
    """A live-empty backtest session (no backup exists yet -- fresh schema)
    plus every fixed-path live state file this script must never touch,
    monkeypatched to tmp_path so a bug that writes to them fails loudly
    instead of silently touching the real files."""
    backups_dir = tmp_path / "backups"
    backtest_dir = tmp_path / "backtest"
    backups_dir.mkdir()
    monkeypatch.setattr(bs, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(bs, "BACKTEST_DIR", backtest_dir)

    params = json.loads(json.dumps(DEFAULT_PARAMS))
    monkeypatch.setattr(executor, "load_params", lambda: params)

    live_state_paths = {
        "RECENT_ORDERS_PATH": tmp_path / "live_recent_orders.json",
        "DAILY_ORDER_COUNT_PATH": tmp_path / "live_daily_order_count.json",
        "PEAK_EQUITY_PATH": tmp_path / "live_peak_equity.json",
    }
    for attr, path in live_state_paths.items():
        monkeypatch.setattr(executor, attr, path)

    manifest = bs.create_session("s1", start_date="2026-06-01", end_date="2026-06-10")
    return {"manifest": manifest, "params": params, "live_state_paths": live_state_paths}


def _buy(session_id="s1", ticker="SOFI", qty=5, price=4.0, timestamp=SIM_TS, **kw):
    argv = [
        "replay_order.py", "--session-id", session_id, "--action", "BUY",
        "--ticker", ticker, "--qty", str(qty), "--price", str(price), "--timestamp", timestamp,
        "--conviction", str(kw.pop("conviction", 0.8)), "--sector", kw.pop("sector", "Financial Services"),
        "--thesis", kw.pop("thesis", "test"),
    ]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    return argv


def _sell(session_id="s1", ticker="SOFI", qty=5, price=4.5, timestamp=SIM_TS, close_reason="target"):
    return [
        "replay_order.py", "--session-id", session_id, "--action", "SELL",
        "--ticker", ticker, "--qty", str(qty), "--price", str(price), "--timestamp", timestamp,
        "--close-reason", close_reason,
    ]


class TestBuy:
    def test_grants_and_records_position(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _buy())
        assert replay_order.main() == 0
        out = json.loads(capsys.readouterr().out)
        assert out["simulated_fill"]["total_shares"] == 5
        assert out["simulated_fill"]["entry_price"] == 4.0

        conn = trader_db.get_conn(Path(session_env["manifest"]["db_path"]))
        pos = trader_db.get_position(conn, "SOFI")
        conn.close()
        assert pos["shares"] == 5
        assert pos["status"] == "open"

    def test_writes_entry_training_example(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _buy(features=json.dumps({"macdh": {"direction": "bullish", "confidence": 0.7}})))
        replay_order.main()

        conn = trader_db.get_conn(Path(session_env["manifest"]["db_path"]))
        rows = conn.execute("SELECT * FROM training_examples").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["example_type"] == "entry"
        assert rows[0]["label_win"] is None

    def test_scale_in_computes_weighted_average_entry_price(self, session_env, monkeypatch, capsys):
        """Regression test for bug 3: entry_price must actually persist
        after a scale-in, not silently stay at the original fill price."""
        monkeypatch.setattr(sys, "argv", _buy(qty=5, price=4.00))
        replay_order.main()
        capsys.readouterr()
        monkeypatch.setattr(sys, "argv", _buy(qty=3, price=4.20))
        replay_order.main()
        capsys.readouterr()

        conn = trader_db.get_conn(Path(session_env["manifest"]["db_path"]))
        pos = trader_db.get_position(conn, "SOFI")
        conn.close()
        assert pos["shares"] == 8
        assert pos["entry_price"] == pytest.approx((5 * 4.00 + 3 * 4.20) / 8)

    def test_uses_simulated_timestamp_not_real_wall_clock_for_hours_gate(self, session_env, monkeypatch, capsys):
        """Regression test for bug 1. SIM_TS is a Wednesday during market
        hours regardless of when this test actually runs (including on a
        real Saturday, like the day this was written) -- if gate_hours
        used the real clock instead of context['_test_now'], this would
        fail whenever the test suite happens to run outside 9:30-16:00 ET
        Mon-Fri."""
        monkeypatch.setattr(sys, "argv", _buy(timestamp=SIM_TS))
        assert replay_order.main() == 0
        out = json.loads(capsys.readouterr().out)
        hours_result = next(g for g in out["gates"] if g["gate"] == "hours")
        assert hours_result["passed"] is True

    def test_rejects_on_real_weekend_simulated_timestamp(self, session_env, monkeypatch, capsys):
        saturday_ts = "2026-06-06T10:30:00-04:00"  # a real Saturday
        monkeypatch.setattr(sys, "argv", _buy(timestamp=saturday_ts))
        assert replay_order.main() == 1
        out = json.loads(capsys.readouterr().out)
        assert "hours" in out["error"]

    def test_gate_rejection_no_position_written(self, session_env, monkeypatch, capsys):
        # position_size cap is 6% of a $10k portfolio = $600; this order costs $10,000
        monkeypatch.setattr(sys, "argv", _buy(ticker="AAPL", qty=50, price=200.0))
        assert replay_order.main() == 1
        out = json.loads(capsys.readouterr().out)
        assert "guardrail" in out["error"]
        assert "gates" in out

        conn = trader_db.get_conn(Path(session_env["manifest"]["db_path"]))
        pos = trader_db.get_position(conn, "AAPL")
        conn.close()
        assert pos is None

    def test_missing_session_fails_closed(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(bs, "BACKUPS_DIR", tmp_path / "backups")
        monkeypatch.setattr(bs, "BACKTEST_DIR", tmp_path / "backtest")
        monkeypatch.setattr(sys, "argv", _buy(session_id="ghost-session"))
        assert replay_order.main() == 1
        out = json.loads(capsys.readouterr().out)
        assert "no backtest session" in out["error"]


class TestThesisPersistence:
    """2026-08-03: --thesis-claim/--thesis-invalidation, mirroring
    executor.py's live-trading equivalent -- the backtest harness needs
    these to actually exercise conviction/long-play thesis persistence
    during a chained-mode multi-day validation session."""

    def test_conviction_play_missing_thesis_invalidation_rejected(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _buy(
            play_type="conviction", prediction_reason="strong momentum",
        ))
        assert replay_order.main() == 1
        out = json.loads(capsys.readouterr().out)
        assert "--play-type conviction requires --thesis-invalidation" in out["error"]

    def test_long_play_missing_thesis_invalidation_rejected(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _buy(
            play_type="long", predicted_by_date="2026-06-10", prediction_reason="catalyst expected",
        ))
        assert replay_order.main() == 1
        out = json.loads(capsys.readouterr().out)
        assert "--play-type long requires --thesis-invalidation" in out["error"]

    def test_conviction_play_persists_thesis_fields(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _buy(
            play_type="conviction", prediction_reason="strong momentum",
            thesis_claim="SOFI re-rates up over 2 weeks on loan growth",
            thesis_invalidation="closes below 20-day MA on rising volume",
            features=json.dumps({"technical": {"direction": "bullish", "confidence": 0.8}}),
        ))
        assert replay_order.main() == 0

        conn = trader_db.get_conn(Path(session_env["manifest"]["db_path"]))
        pos = trader_db.get_position(conn, "SOFI")
        log = trader_db.get_thesis_log(conn, "SOFI")
        conn.close()
        assert pos["thesis_claim"] == "SOFI re-rates up over 2 weeks on loan growth"
        assert pos["thesis_invalidation"] == "closes below 20-day MA on rising volume"
        assert pos["thesis_entry_signals"] == json.dumps({"technical": {"direction": "bullish", "confidence": 0.8}})
        assert len(log) == 1
        assert log[0]["event_type"] == "entry"

    def test_standard_buy_thesis_invalidation_optional(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _buy())  # standard, no thesis-claim/invalidation
        assert replay_order.main() == 0

    def test_scale_in_logs_scale_in_event(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _buy(
            play_type="conviction", prediction_reason="x",
            thesis_claim="initial thesis", thesis_invalidation="y",
        ))
        replay_order.main()
        capsys.readouterr()
        monkeypatch.setattr(sys, "argv", _buy(
            qty=3, price=4.20, play_type="conviction", prediction_reason="x",
            thesis_claim="adding on strength", thesis_invalidation="y",
        ))
        replay_order.main()

        conn = trader_db.get_conn(Path(session_env["manifest"]["db_path"]))
        log = trader_db.get_thesis_log(conn, "SOFI")
        conn.close()
        assert len(log) == 2
        assert log[0]["event_type"] == "scale_in"
        assert log[1]["event_type"] == "entry"


class TestBacktestDisabledGatesDontTouchLiveState:
    """Regression tests for bug 2. Every one of these gates would read
    and/or WRITE a fixed-path live state file if it ran for real --
    confirmed live during manual testing (gate_drawdown_circuit_breaker
    silently wrote to the real state/peak_equity.json). Pre-seed each
    live state file with a sentinel value and confirm it's byte-for-byte
    untouched after a backtest BUY."""

    def test_none_of_the_disabled_gates_touch_their_live_files(self, session_env, monkeypatch, capsys):
        sentinels = {}
        for attr, path in session_env["live_state_paths"].items():
            path.write_text('{"sentinel": true}')
            sentinels[path] = path.read_text()

        monkeypatch.setattr(sys, "argv", _buy())
        assert replay_order.main() == 0

        for path, original in sentinels.items():
            assert path.exists(), f"{path} was deleted"
            assert path.read_text() == original, f"{path} was modified by a supposedly-disabled gate"

    def test_disabled_gates_show_as_disabled_in_output(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _buy())
        replay_order.main()
        out = json.loads(capsys.readouterr().out)
        for gate_name in replay_order.BACKTEST_DISABLED_GATES:
            result = next(g for g in out["gates"] if g["gate"] == gate_name)
            assert "disabled via params.json" in result["reason"]

    def test_explicit_toggle_wins_even_if_live_params_json_has_gate_enabled(self, session_env):
        """The whole safety property depends on the override always
        applying, not just when params.json happens to already have the
        gate off -- confirm DEFAULT_PARAMS has them all True/enabled and
        _backtest_toggles() still disables every one."""
        for gate_name in replay_order.BACKTEST_DISABLED_GATES:
            assert DEFAULT_GUARDRAIL_GATES[gate_name] is True  # live default is ON
        toggles = replay_order._backtest_toggles()
        for gate_name in replay_order.BACKTEST_DISABLED_GATES:
            assert toggles[gate_name] is False


class TestSell:
    def test_full_close_labels_pnl_correctly(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _buy(qty=5, price=4.00))
        replay_order.main()
        capsys.readouterr()

        monkeypatch.setattr(sys, "argv", _sell(qty=5, price=4.50))
        assert replay_order.main() == 0
        out = json.loads(capsys.readouterr().out)
        assert out["simulated_fill"]["pnl"] == pytest.approx((4.50 - 4.00) * 5)
        assert out["simulated_fill"]["return_pct"] == pytest.approx(12.5)
        assert out["simulated_fill"]["remaining_shares"] == 0

        conn = trader_db.get_conn(Path(session_env["manifest"]["db_path"]))
        pos = trader_db.get_position(conn, "SOFI")
        conn.close()
        assert pos["status"] == "closed"

    def test_pnl_uses_scaled_in_weighted_entry_price_not_original(self, session_env, monkeypatch, capsys):
        """Regression test for bug 3, end to end through a real SELL."""
        monkeypatch.setattr(sys, "argv", _buy(qty=5, price=4.00))
        replay_order.main()
        capsys.readouterr()
        monkeypatch.setattr(sys, "argv", _buy(qty=3, price=4.20))
        replay_order.main()
        capsys.readouterr()

        monkeypatch.setattr(sys, "argv", _sell(qty=8, price=4.50))
        replay_order.main()
        out = json.loads(capsys.readouterr().out)

        weighted_entry = (5 * 4.00 + 3 * 4.20) / 8
        assert out["simulated_fill"]["pnl"] == pytest.approx((4.50 - weighted_entry) * 8)

    def test_labels_the_entry_row_not_the_exit_row(self, session_env, monkeypatch, capsys):
        """Regression test for bug 4 -- the exact mislabeling shape fixed
        elsewhere this morning, reintroduced via call ordering here."""
        monkeypatch.setattr(
            sys, "argv",
            _buy(features=json.dumps({"macdh": {"direction": "bullish", "confidence": 0.7}})),
        )
        replay_order.main()
        capsys.readouterr()
        monkeypatch.setattr(sys, "argv", _sell())
        replay_order.main()
        capsys.readouterr()

        conn = trader_db.get_conn(Path(session_env["manifest"]["db_path"]))
        rows = {r["example_type"]: dict(r) for r in conn.execute("SELECT * FROM training_examples")}
        conn.close()

        assert "entry" in rows and "exit" in rows
        assert rows["entry"]["label_win"] == 1
        assert rows["entry"]["label_return_pct"] is not None
        assert rows["exit"]["label_win"] is None, "the exit row must NOT be the one that gets labeled"

    def test_partial_sell_leaves_remainder_open(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _buy(qty=10, price=4.00))
        replay_order.main()
        capsys.readouterr()

        monkeypatch.setattr(sys, "argv", _sell(qty=4, price=4.50))
        replay_order.main()
        out = json.loads(capsys.readouterr().out)
        assert out["simulated_fill"]["remaining_shares"] == 6

        conn = trader_db.get_conn(Path(session_env["manifest"]["db_path"]))
        pos = trader_db.get_position(conn, "SOFI")
        conn.close()
        assert pos["status"] == "open"
        assert pos["shares"] == 6

    def test_sell_with_no_open_position_errors(self, session_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", _sell(ticker="GHOST"))
        assert replay_order.main() == 1
        out = json.loads(capsys.readouterr().out)
        assert "no open backtest position" in out["error"]


class TestIsolationFromLiveDb:
    def test_never_touches_state_trader_db(self, session_env, monkeypatch, capsys, tmp_path):
        """The single most important property of the whole design: a
        backtest order must never write to the real state/trader.db, no
        matter what. Points trader_db.DB_PATH itself at a canary file and
        confirms replay_order.py's session-scoped db_path override means
        that canary is never touched."""
        canary = tmp_path / "would_be_live_trader_db.db"
        monkeypatch.setattr(trader_db, "DB_PATH", canary)

        monkeypatch.setattr(sys, "argv", _buy())
        assert replay_order.main() == 0

        assert not canary.exists(), "replay_order.py touched what would be the live trader.db"
