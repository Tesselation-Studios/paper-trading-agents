#!/usr/bin/env python3
"""
Unit tests for scripts/prepare_tick_replay.py -- single-day mode (pre-existing,
previously untested) and the new chained --session-id mode (2026-08-02) that
walks forward through a backtest_session.py session, carrying portfolio state
day to day. Real sqlite3/parquet files under tmp_path, no mocking of trader_db
or backtest_session internals.
"""
import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import prepare_tick_replay as ptr  # noqa: E402
import backtest_session as bs  # noqa: E402
import trader_db  # noqa: E402

UNIVERSE = ["AAA", "BBB", "CCC", "DDD", "EEE"]  # 5 == MIN_SYMBOLS_PER_REPLAY_DAY
TRADING_DAYS = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]  # Mon-Fri, no holidays


def _write_bars(cache_dir: Path, symbol: str, days: list[str], base_price: float = 100.0) -> None:
    """One row per prepare_tick_replay.TICK_TIMES slot per day -- an exact
    match for every sampled tick, so tests don't depend on _snapshot_at's
    'most recent prior bar' fallback behavior."""
    import market_hours
    rows = []
    for day_str in days:
        day = date.fromisoformat(day_str)
        for t in ptr.TICK_TIMES:
            ts = datetime.combine(day, t, tzinfo=market_hours.ET)
            rows.append({
                "timestamp": ts, "open": base_price, "high": base_price, "low": base_price,
                "close": base_price, "volume": 10_000, "rsi_14": 50.0, "macd_hist": 0.1,
            })
        # also an explicit EOD close row at 16:00
        rows.append({
            "timestamp": datetime.combine(day, dtime(16, 0), tzinfo=market_hours.ET),
            "open": base_price, "high": base_price, "low": base_price,
            "close": base_price, "volume": 10_000, "rsi_14": 50.0, "macd_hist": 0.1,
        })
    df = pd.DataFrame(rows)
    df.to_parquet(cache_dir / f"{symbol}.parquet")


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache_dir = tmp_path / "bars"
    cache_dir.mkdir()
    for sym in UNIVERSE:
        _write_bars(cache_dir, sym, TRADING_DAYS)

    backups_dir = tmp_path / "backups"
    backtest_dir = tmp_path / "backtest"
    backups_dir.mkdir()

    output_path = tmp_path / "tick_replay_latest.json"
    progress_path = tmp_path / "tick_replay_progress.json"

    monkeypatch.setattr(ptr, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(ptr, "OUTPUT_PATH", output_path)
    monkeypatch.setattr(ptr, "PROGRESS_PATH", progress_path)
    monkeypatch.setattr(ptr, "current_universe", lambda: list(UNIVERSE))
    monkeypatch.setattr(bs, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(bs, "BACKTEST_DIR", backtest_dir)

    return {"cache_dir": cache_dir, "output_path": output_path, "progress_path": progress_path,
            "backups_dir": backups_dir, "backtest_dir": backtest_dir}


# ── Single-day mode (pre-existing behavior, previously untested) ──────────


class TestSingleDayMode:
    def test_picks_most_recent_unreplayed_day(self, env, capsys):
        rc = ptr.main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"status": "ok"' in out
        assert '"mode": "single-day"' in out
        result = __import__("json").loads(env["output_path"].read_text())
        assert result["date"] == TRADING_DAYS[-1]  # most recent first

    def test_second_run_picks_a_different_day(self, env):
        ptr.main([])
        first = __import__("json").loads(env["output_path"].read_text())["date"]
        ptr.main([])
        second = __import__("json").loads(env["output_path"].read_text())["date"]
        assert first != second

    def test_exhausts_all_days_then_skips(self, env, capsys):
        for _ in TRADING_DAYS:
            ptr.main([])
        capsys.readouterr()
        rc = ptr.main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"status": "skipped"' in out
        assert "no unreplayed" in out

    def test_empty_universe_skips(self, env, monkeypatch, capsys):
        monkeypatch.setattr(ptr, "current_universe", lambda: [])
        rc = ptr.main([])
        assert rc == 0
        assert '"reason": "empty universe"' in capsys.readouterr().out


# ── Chained mode ────────────────────────────────────────────────────────


class TestChainedModeCreatesSession:
    def test_first_call_creates_session_at_earliest_available_day(self, env):
        rc = ptr.main(["--session-id", "chain-1"])
        assert rc == 0
        manifest = bs.get_session("chain-1")
        assert manifest is not None
        assert manifest["status"] == "active"
        assert manifest["start_date"] == TRADING_DAYS[0]  # earliest, ascending
        assert manifest["day_in_progress"] == TRADING_DAYS[0]
        assert manifest["current_date"] is None  # not completed yet

    def test_replay_file_carries_session_id_and_portfolio(self, env):
        ptr.main(["--session-id", "chain-1"])
        import json
        result = json.loads(env["output_path"].read_text())
        assert result["mode"] == "chained"
        assert result["session_id"] == "chain-1"
        assert result["portfolio"]["cash"] == bs.STARTING_CASH_DEFAULT
        assert result["portfolio"]["open_positions"] == []
        assert result["date"] == TRADING_DAYS[0]

    def test_explicit_start_date_respected(self, env):
        ptr.main(["--session-id", "chain-1", "--start-date", TRADING_DAYS[2]])
        manifest = bs.get_session("chain-1")
        assert manifest["start_date"] == TRADING_DAYS[2]
        assert manifest["day_in_progress"] == TRADING_DAYS[2]


class TestChainedModeAdvancement:
    def test_retries_same_day_when_in_progress_not_completed(self, env):
        ptr.main(["--session-id", "chain-1"])
        first_day = bs.get_session("chain-1")["day_in_progress"]
        # Simulate a crashed/timed-out fire: never call complete_day, prepare again.
        ptr.main(["--session-id", "chain-1"])
        manifest = bs.get_session("chain-1")
        assert manifest["day_in_progress"] == first_day  # unchanged, not advanced

    def test_advances_to_next_day_after_completion(self, env):
        ptr.main(["--session-id", "chain-1"])
        day1 = bs.get_session("chain-1")["day_in_progress"]
        bs.complete_day("chain-1", day1)

        ptr.main(["--session-id", "chain-1"])
        manifest = bs.get_session("chain-1")
        assert manifest["current_date"] == day1
        assert manifest["day_in_progress"] == TRADING_DAYS[TRADING_DAYS.index(day1) + 1]

    def test_chain_exhausted_after_last_day_skips_cleanly(self, env, capsys):
        for i in range(len(TRADING_DAYS)):
            ptr.main(["--session-id", "chain-1"])
            day = bs.get_session("chain-1")["day_in_progress"]
            bs.complete_day("chain-1", day)

        capsys.readouterr()
        rc = ptr.main(["--session-id", "chain-1"])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"status": "skipped"' in out
        assert "exhausted" in out

    def test_end_date_caps_the_chain_early(self, env):
        ptr.main(["--session-id", "chain-1", "--end-date", TRADING_DAYS[1]])
        day1 = bs.get_session("chain-1")["day_in_progress"]
        bs.complete_day("chain-1", day1)

        ptr.main(["--session-id", "chain-1"])
        manifest = bs.get_session("chain-1")
        assert manifest["day_in_progress"] == TRADING_DAYS[1]
        bs.complete_day("chain-1", TRADING_DAYS[1])

        manifest = bs.get_session("chain-1")
        assert manifest["status"] == "completed"  # complete_day() itself closes it at end_date

    def test_completed_session_is_not_reopened(self, env, capsys):
        ptr.main(["--session-id", "chain-1", "--end-date", TRADING_DAYS[0]])
        day1 = bs.get_session("chain-1")["day_in_progress"]
        bs.complete_day("chain-1", day1)
        assert bs.get_session("chain-1")["status"] == "completed"

        capsys.readouterr()
        rc = ptr.main(["--session-id", "chain-1"])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"status": "skipped"' in out
        assert "not active" in out


class TestChainedModePortfolioContinuity:
    def test_open_position_carries_into_next_days_replay_file(self, env):
        import json
        ptr.main(["--session-id", "chain-1"])
        day1 = bs.get_session("chain-1")["day_in_progress"]

        db_path = Path(bs.get_session("chain-1")["db_path"])
        conn = trader_db.get_conn(db_path)
        with conn:
            trader_db.upsert_position(conn, ticker="AAA", shares=10.0, entry_price=100.0, entry_time=f"{day1}T10:00:00Z")
        conn.close()
        bs.complete_day("chain-1", day1)

        ptr.main(["--session-id", "chain-1"])
        result = json.loads(env["output_path"].read_text())
        tickers = [p["ticker"] for p in result["portfolio"]["open_positions"]]
        assert "AAA" in tickers
        # cash reflects the $1000 cost basis no longer sitting idle
        assert result["portfolio"]["cash"] == pytest.approx(bs.STARTING_CASH_DEFAULT - 1000.0)

    def test_no_cross_contamination_across_two_independent_chains(self, env):
        import json
        ptr.main(["--session-id", "chain-a"])
        ptr.main(["--session-id", "chain-b"])

        db_a = Path(bs.get_session("chain-a")["db_path"])
        db_b = Path(bs.get_session("chain-b")["db_path"])
        assert db_a != db_b

        conn = trader_db.get_conn(db_a)
        with conn:
            trader_db.upsert_position(conn, ticker="AAA", shares=5.0, entry_price=50.0, entry_time="t1")
        conn.close()

        conn_b = trader_db.get_conn(db_b)
        try:
            assert trader_db.get_open_positions(conn_b) == []
        finally:
            conn_b.close()


class TestChainedModeNoDataAvailable:
    def test_no_cached_days_skips_without_creating_session(self, env, monkeypatch, capsys):
        monkeypatch.setattr(ptr, "current_universe", lambda: ["ZZZ"])  # no parquet file for ZZZ
        rc = ptr.main(["--session-id", "chain-empty"])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"status": "skipped"' in out
        assert bs.get_session("chain-empty") is None


# ── Experiment mode (2026-08-02) ───────────────────────────────────────────


class TestExperimentMode:
    def test_explicit_date_and_params_pass_through(self, env):
        import json
        rc = ptr.main(["--experiment-id", "exp1", "--date", TRADING_DAYS[2],
                        "--params", '{"scorecard_override": {"technical": {"hit_rate": 0.8}}}'])
        assert rc == 0
        result = json.loads(env["output_path"].read_text())
        assert result["mode"] == "experiment"
        assert result["experiment_id"] == "exp1"
        assert result["date"] == TRADING_DAYS[2]
        assert result["params"] == {"scorecard_override": {"technical": {"hit_rate": 0.8}}}
        assert "run_id" in result

    def test_missing_date_errors(self, capsys, env):
        rc = ptr.main(["--experiment-id", "exp1"])
        assert rc == 1
        out = capsys.readouterr().out
        assert '"status": "error"' in out
        assert "requires --date" in out

    def test_invalid_params_json_errors(self, capsys, env):
        rc = ptr.main(["--experiment-id", "exp1", "--date", TRADING_DAYS[0], "--params", "{not json"])
        assert rc == 1
        out = capsys.readouterr().out
        assert '"status": "error"' in out

    def test_same_date_can_be_run_repeatedly_with_different_params(self, env):
        import json
        ptr.main(["--experiment-id", "exp1", "--date", TRADING_DAYS[0], "--params", '{"weight": 1.0}'])
        result1 = json.loads(env["output_path"].read_text())
        ptr.main(["--experiment-id", "exp1", "--date", TRADING_DAYS[0], "--params", '{"weight": 2.0}'])
        result2 = json.loads(env["output_path"].read_text())
        assert result1["date"] == result2["date"] == TRADING_DAYS[0]
        assert result1["params"] == {"weight": 1.0}
        assert result2["params"] == {"weight": 2.0}

    def test_experiment_run_never_touches_progress_file(self, env):
        """The whole point of experiment mode is repeatability -- it must never
        mark a date as 'replayed', unlike single-day mode, or a second run on
        the same date would silently behave differently."""
        assert not env["progress_path"].exists()
        ptr.main(["--experiment-id", "exp1", "--date", TRADING_DAYS[0], "--params", "{}"])
        assert not env["progress_path"].exists()

    def test_date_not_in_cache_skips(self, env, capsys):
        rc = ptr.main(["--experiment-id", "exp1", "--date", "2026-01-01", "--params", "{}"])
        assert rc == 0
        out = capsys.readouterr().out
        assert '"status": "skipped"' in out

    def test_default_params_is_empty_object(self, env):
        import json
        ptr.main(["--experiment-id", "exp1", "--date", TRADING_DAYS[0]])
        result = json.loads(env["output_path"].read_text())
        assert result["params"] == {}
