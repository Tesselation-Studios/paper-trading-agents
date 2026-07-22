#!/usr/bin/env python3
"""
Unit tests for scripts/replay_check.py's risk-adjusted scoring
(resample_to_daily_equity, compute_risk_metrics) — added 2026-07-22 as
Phase 7 groundwork, since ReplayResult previously only exposed win_rate/
total_return_pct, no risk-adjusted metric despite "win rate isn't the
metric that matters" being a proven finding.

Pure math on a synthetic ReplayResult — no network, no Alpaca creds
needed, doesn't hit the `integration` marker.
"""
import datetime
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, "/home/openclaw/projects/paper-trading-rebuild")

import replay_check  # noqa: E402
from src.replay import ReplayResult  # noqa: E402


def make_result(equity_values, timestamps=None):
    """Build a minimal ReplayResult for scoring tests."""
    if timestamps is None:
        base = datetime.datetime(2026, 1, 1)
        timestamps = [base + datetime.timedelta(days=i) for i in range(len(equity_values))]
    equity = np.array(equity_values, dtype=np.float64)
    return ReplayResult(
        equity_curve=equity,
        returns=np.array([0.0] * len(equity)),
        trades=[],
        initial_balance=float(equity_values[0]),
        final_equity=float(equity_values[-1]),
        total_pnl=float(equity_values[-1] - equity_values[0]),
        total_return_pct=(equity_values[-1] - equity_values[0]) / equity_values[0] * 100,
        n_ticks=len(equity),
        n_decisions=0,
        tickers_seen=["A"],
        timestamps=timestamps,
    )


class TestResampleToDailyEquity:
    def test_one_tick_per_day_passthrough(self):
        result = make_result([100, 101, 99, 105])
        daily = replay_check.resample_to_daily_equity(result)
        assert daily == [100, 101, 99, 105]

    def test_multiple_ticks_per_day_keeps_last(self):
        """Multi-ticker interleaved replay produces several entries per
        calendar day (one per ticker) — only the last chronological value
        for each day should survive, not an average or the first."""
        base = datetime.datetime(2026, 1, 1)
        timestamps = [
            base.replace(hour=9), base.replace(hour=15),   # day 1: two ticks
            (base + datetime.timedelta(days=1)).replace(hour=10),  # day 2: one tick
        ]
        result = make_result([100, 102, 110], timestamps=timestamps)
        daily = replay_check.resample_to_daily_equity(result)
        assert daily == [102, 110]  # not [100, 110] or [101, 110]

    def test_no_timestamps_returns_none(self):
        result = make_result([100, 101])
        result.timestamps = []
        daily = replay_check.resample_to_daily_equity(result)
        assert daily is None

    def test_mismatched_lengths_returns_none(self):
        result = make_result([100, 101, 102])
        result.timestamps = result.timestamps[:1]  # deliberately mismatched
        daily = replay_check.resample_to_daily_equity(result)
        assert daily is None


class TestComputeRiskMetrics:
    def test_insufficient_history_reports_none(self):
        result = make_result([100])  # single day, can't compute returns
        metrics = replay_check.compute_risk_metrics(result)
        assert metrics["sharpe"] is None
        assert metrics["sortino"] is None
        assert "note" in metrics

    def test_flat_equity_zero_std_gives_none_sharpe(self):
        """Zero volatility (no gains or losses) shouldn't divide by zero —
        should report None rather than crash or return inf/nan."""
        result = make_result([100, 100, 100, 100, 100])
        metrics = replay_check.compute_risk_metrics(result)
        assert metrics["sharpe"] is None
        assert metrics["max_drawdown_pct"] == 0.0

    def test_steady_uptrend_positive_sharpe(self):
        result = make_result([100, 102, 104, 106, 108, 110])
        metrics = replay_check.compute_risk_metrics(result)
        assert metrics["sharpe"] > 0
        assert metrics["sortino"] is None or metrics["sortino"] >= 0  # no down days -> None or non-negative
        assert metrics["max_drawdown_pct"] == 0.0  # monotonic up, no drawdown

    def test_drawdown_detected(self):
        # Peak at 110 (index 2), trough at 90 (index 4) -> ~18.2% drawdown
        result = make_result([100, 105, 110, 100, 90, 95])
        metrics = replay_check.compute_risk_metrics(result)
        assert metrics["max_drawdown_pct"] < -15
        assert metrics["max_drawdown_pct"] > -20

    def test_losing_strategy_negative_sharpe(self):
        result = make_result([100, 95, 90, 85, 80, 75])
        metrics = replay_check.compute_risk_metrics(result)
        assert metrics["sharpe"] < 0
        assert metrics["calmar"] is not None and metrics["calmar"] < 0

    def test_sortino_only_penalizes_downside(self):
        """A series with the same overall volatility but all moves in one
        direction should have very different Sharpe vs Sortino behavior —
        Sortino ignores upside variance entirely."""
        # Mostly up with a couple of down days mixed in
        result = make_result([100, 103, 101, 105, 103, 108, 106, 112])
        metrics = replay_check.compute_risk_metrics(result)
        assert metrics["sharpe"] is not None
        assert metrics["sortino"] is not None
        # Sortino should be higher than Sharpe here since downside moves are
        # a small subset of total volatility (fewer, smaller down days).
        assert metrics["sortino"] >= metrics["sharpe"]

    def test_summarize_includes_risk_metrics(self):
        result = make_result([100, 102, 98, 105, 110])
        summary = replay_check.summarize(result, "test-variant")
        assert "sharpe" in summary
        assert "sortino" in summary
        assert "calmar" in summary
        assert "max_drawdown_pct" in summary
        assert summary["variant"] == "test-variant"
