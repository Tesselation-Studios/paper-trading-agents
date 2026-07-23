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
import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, "/home/openclaw/projects/paper-trading-rebuild")

import replay_check  # noqa: E402
from src.replay import ReplayResult, Tick  # noqa: E402


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


class TestLoadLiveUniverse:
    """load_live_universe() builds the default replay ticker list from
    positions/*.md + active watchlist candidates instead of a hand-picked
    static list, so nightly comparisons can't be accidentally cherry-picked.
    Added 2026-07-22 alongside the v1.0-is-live-strategy clarification.
    """

    def _setup(self, tmp_path, monkeypatch, positions=(), watchlist_text=None):
        (tmp_path / "positions").mkdir()
        for ticker in positions:
            (tmp_path / "positions" / f"{ticker}.md").write_text("# position\n")
        (tmp_path / "strategies").mkdir()
        if watchlist_text is not None:
            (tmp_path / "strategies" / "watchlist.md").write_text(watchlist_text)
        monkeypatch.setattr(replay_check, "REPO_ROOT", tmp_path)

    def test_combines_positions_and_active_candidates(self, tmp_path, monkeypatch):
        watchlist = (
            "## Currently Held\n"
            "- NVDA — open position\n"
            "## Candidates\n"
            "- RKT — idle_ticks: 10 — note\n"
            "- CLF — idle_ticks: 10 — note\n"
        )
        self._setup(tmp_path, monkeypatch, positions=["NVDA", "CHWY"], watchlist_text=watchlist)
        assert replay_check.load_live_universe() == ["CHWY", "CLF", "NVDA", "RKT"]

    def test_struck_through_candidates_excluded(self, tmp_path, monkeypatch):
        watchlist = (
            "## Candidates\n"
            "- RKT — idle_ticks: 10 — note\n"
            "- ~~SOFI~~ — closed 2026-07-22 (broke support)\n"
        )
        self._setup(tmp_path, monkeypatch, positions=[], watchlist_text=watchlist)
        assert replay_check.load_live_universe() == ["RKT"]

    def test_stops_reading_candidates_at_next_heading(self, tmp_path, monkeypatch):
        watchlist = (
            "## Candidates\n"
            "- RKT — idle_ticks: 10 — note\n"
            "## Some Other Section\n"
            "- ZZZZ — should not be picked up, past the Candidates section\n"
        )
        self._setup(tmp_path, monkeypatch, positions=[], watchlist_text=watchlist)
        assert replay_check.load_live_universe() == ["RKT"]

    def test_falls_back_to_static_list_when_nothing_live(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, positions=[], watchlist_text="## Candidates\n")
        assert replay_check.load_live_universe() == replay_check.FALLBACK_TICKERS

    def test_missing_watchlist_file_still_uses_positions(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, positions=["GME"], watchlist_text=None)
        assert replay_check.load_live_universe() == ["GME"]


def make_tick(ticker, day, rsi=50.0):
    ts = datetime.datetime(2026, 1, 1) + datetime.timedelta(days=day)
    return Tick(timestamp=ts, ticker=ticker, open=10.0, high=10.0, low=10.0,
                close=10.0, volume=1000, rsi=rsi)


class TestSplitTicksByMidpoint:
    """split_ticks_by_midpoint() added 2026-07-23 for robustness-checking
    a strategy comparison across two halves of the lookback window, not
    just one aggregate number over the whole thing."""

    def test_empty_input_returns_two_empty_lists(self):
        first, second = replay_check.split_ticks_by_midpoint([])
        assert first == []
        assert second == []

    def test_splits_by_date_midpoint_not_index_count(self):
        # 10 distinct days, single ticker one tick/day -> midpoint is day 5.
        ticks = [make_tick("AAA", d) for d in range(10)]
        first, second = replay_check.split_ticks_by_midpoint(ticks)
        assert all(t.timestamp < ticks[5].timestamp for t in first)
        assert all(t.timestamp >= ticks[5].timestamp for t in second)
        assert len(first) + len(second) == 10

    def test_multi_ticker_same_day_stays_together(self):
        # Two tickers both have a tick on day 0-3; date midpoint should
        # split by the DATE, keeping same-day ticks from different
        # tickers on the same side, not skewed by tick count per ticker.
        ticks = [make_tick("AAA", d) for d in range(4)] + [make_tick("BBB", d) for d in range(4)]
        first, second = replay_check.split_ticks_by_midpoint(ticks)
        first_days = {t.timestamp for t in first}
        second_days = {t.timestamp for t in second}
        assert first_days.isdisjoint(second_days)
        assert len(first) == 4  # days 0,1 x 2 tickers
        assert len(second) == 4  # days 2,3 x 2 tickers

    def test_halves_are_chronologically_ordered_relative_to_each_other(self):
        ticks = [make_tick("AAA", d) for d in range(6)]
        first, second = replay_check.split_ticks_by_midpoint(ticks)
        assert max(t.timestamp for t in first) < min(t.timestamp for t in second)


def make_sweep_frame(n_days=20, entry_day=1, daily_pct_change=-0.02):
    """A single-ticker synthetic frame with all columns make_trader's
    lookup needs (macd_hist/line/signal, ma20/ma50, vol_20d) — enters the
    RSI 45-65 band on entry_day, then closes drift by daily_pct_change
    every day after, so different stop_loss/profit_target thresholds
    trigger an exit on different days (or not at all).
    """
    base = datetime.datetime(2026, 1, 1)
    rows = []
    price = 100.0
    for d in range(n_days):
        rsi = 55.0 if d == entry_day else 50.0  # only in-band on entry_day
        rows.append({
            "timestamp": base + datetime.timedelta(days=d), "close": price,
            "high": price, "low": price, "volume": 10000,
            "rsi_14": rsi, "macd_line": 0.1, "macd_hist": 0.1, "macd_signal": 0.0,
            "ma20": price, "ma50": price, "vol_20d": 0.01,
        })
        if d >= entry_day:
            price = price * (1 + daily_pct_change)
    return pd.DataFrame(rows)


class TestSweepThresholds:
    """sweep_thresholds() added 2026-07-23 as evolution-batch groundwork —
    small grid over stop_loss_pct/profit_target_pct, scored by the same
    both-halves-Sharpe-positive robustness bar the v1.1 decision used."""

    def test_returns_one_candidate_per_grid_combo(self):
        frames = {"AAA": make_sweep_frame()}
        ticks = replay_check.build_tick_stream(frames)
        candidates = replay_check.sweep_thresholds(
            frames, ticks, stop_loss_grid=[-15.0, -10.0], profit_target_grid=[10.0, 15.0, 20.0])
        assert len(candidates) == 6  # 2 x 3

    def test_candidate_keys_and_grid_values_round_trip(self):
        frames = {"AAA": make_sweep_frame()}
        ticks = replay_check.build_tick_stream(frames)
        candidates = replay_check.sweep_thresholds(
            frames, ticks, stop_loss_grid=[-8.0], profit_target_grid=[12.0])
        assert len(candidates) == 1
        c = candidates[0]
        assert c["stop_loss_pct"] == -8.0
        assert c["profit_target_pct"] == 12.0
        for key in ("sharpe", "first_half_sharpe", "second_half_sharpe", "robust",
                    "total_return_pct", "n_trades"):
            assert key in c

    def test_tighter_stop_loss_exits_sooner_changes_outcome(self):
        # Steady -2%/day decline after entry -> a tight stop should exit
        # much earlier (fewer days held, different P&L) than a loose one.
        frames = {"AAA": make_sweep_frame(n_days=30, entry_day=1, daily_pct_change=-0.02)}
        ticks = replay_check.build_tick_stream(frames)
        candidates = replay_check.sweep_thresholds(
            frames, ticks, stop_loss_grid=[-5.0, -25.0], profit_target_grid=[50.0])
        by_stop = {c["stop_loss_pct"]: c for c in candidates}
        # Tight stop should produce a worse (more negative) or at least
        # different total return than the loose stop on a declining path.
        assert by_stop[-5.0]["total_return_pct"] != by_stop[-25.0]["total_return_pct"]

    def test_sorted_robust_candidates_ranked_before_non_robust(self):
        frames = {"AAA": make_sweep_frame(n_days=40, entry_day=1, daily_pct_change=0.015)}
        ticks = replay_check.build_tick_stream(frames)
        candidates = replay_check.sweep_thresholds(
            frames, ticks, stop_loss_grid=[-10.0], profit_target_grid=[5.0, 50.0])
        robust_flags = [c["robust"] for c in candidates]
        # Once sorted, True (robust) values must not appear after False ones.
        assert robust_flags == sorted(robust_flags, reverse=True)

    def test_make_trader_uses_overridden_thresholds_not_module_defaults(self):
        # A -3% stop should exit long before the module default -10%, on a
        # steadily declining path, given the same entry.
        frames = {"AAA": make_sweep_frame(n_days=15, entry_day=1, daily_pct_change=-0.01)}
        ticks = replay_check.build_tick_stream(frames)
        tight_trader = replay_check.make_trader(frames, "v1.0", stop_loss_pct=-2.0)
        loose_trader = replay_check.make_trader(frames, "v1.0", stop_loss_pct=-9.0)
        tight_result = replay_check.replay_trader(ticks, tight_trader, initial_balance=10_000.0)
        loose_result = replay_check.replay_trader(ticks, loose_trader, initial_balance=10_000.0)
        assert len(tight_result.trades) >= 1
        assert len(loose_result.trades) >= 1
        # The tight stop should cut the loss earlier -> smaller magnitude loss.
        assert tight_result.trades[0].return_pct > loose_result.trades[0].return_pct
