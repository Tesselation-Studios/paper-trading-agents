#!/usr/bin/env python3
"""
Unit tests for scripts/strategy_version_replay.py — pure logic (git-ref
text extraction, split-window LLM-call reuse, comparison assembly). No
real agent invocation or git subprocess — both are monkeypatched,
matching test_llm_replay.py's no-network test convention.
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import strategy_version_replay as svr  # noqa: E402
from replay_harness import TraderDecision  # noqa: E402


def make_frame(ticker_days):
    """ticker_days: list of (day_offset, rsi) for a single synthetic ticker."""
    base = datetime.datetime(2026, 1, 1)
    rows = []
    for d, rsi in ticker_days:
        rows.append({
            "timestamp": base + datetime.timedelta(days=d), "close": 100.0, "high": 100.0,
            "low": 100.0, "volume": 1000, "rsi_14": rsi, "macd_line": 0.1, "macd_hist": 0.1,
            "macd_signal": 0.05, "ma20": 100.0, "ma50": 100.0, "vol_20d": 0.01,
        })
    return pd.DataFrame(rows)


class TestGetStrategyTextAtRef:
    def test_returns_stdout_on_success(self, monkeypatch):
        def fake_run(cmd, cwd=None, capture_output=None, text=None):
            assert cmd == ["git", "show", "abc123:strategy.md"]
            return subprocess.CompletedProcess(cmd, 0, stdout="# Strategy v1.0", stderr="")
        monkeypatch.setattr(svr.subprocess, "run", fake_run)
        assert svr.get_strategy_text_at_ref("abc123") == "# Strategy v1.0"

    def test_raises_with_stderr_on_failure(self, monkeypatch):
        def fake_run(cmd, cwd=None, capture_output=None, text=None):
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: bad revision")
        monkeypatch.setattr(svr.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="fatal: bad revision"):
            svr.get_strategy_text_at_ref("nonexistent-ref")


class TestRunVariantSplitWindow:
    def test_reuses_cache_for_split_halves_no_extra_llm_calls(self, monkeypatch):
        """The core cost property this script exists to preserve: split-
        window scoring must not double (or triple) the real LLM call count
        versus a single full-window pass -- see module docstring."""
        frames = {"AAA": make_frame([(0, 55.0), (1, 55.0), (2, 55.0), (3, 55.0)])}
        call_count = []

        def fake_invoke(prompt, model=None, timeout=None):
            call_count.append(1)
            return '{"AAA": {"decision": "HOLD"}}'

        monkeypatch.setattr(svr.llm_replay, "invoke_agent", fake_invoke)
        ticks = svr.replay_check.build_tick_stream(frames)

        result = svr.run_variant_split_window("test-version", "strategy text", frames, ticks, model="m")

        # 4 distinct simulated days -> 4 real calls total, regardless of
        # also being re-walked via the first/second half split passes.
        assert len(call_count) == 4
        assert result["version"] == "test-version"
        assert "sharpe" in result
        assert "first_half_sharpe" in result
        assert "second_half_sharpe" in result
        assert "robust" in result

    def test_robust_true_when_both_halves_positive_sharpe(self, monkeypatch):
        frames = {"AAA": make_frame([(0, 55.0), (1, 55.0)])}
        monkeypatch.setattr(svr.llm_replay, "invoke_agent",
                             lambda *a, **k: '{"AAA": {"decision": "HOLD"}}')
        ticks = svr.replay_check.build_tick_stream(frames)

        def fake_compute_risk_metrics(result, risk_free_rate=0.0):
            return {"sharpe": 1.0, "sortino": None, "calmar": None, "max_drawdown_pct": -1.0}

        monkeypatch.setattr(svr.replay_check, "compute_risk_metrics", fake_compute_risk_metrics)
        result = svr.run_variant_split_window("v", "s", frames, ticks, model="m")
        assert result["robust"] is True

    def test_robust_false_when_either_half_missing_sharpe(self, monkeypatch):
        frames = {"AAA": make_frame([(0, 55.0)])}
        monkeypatch.setattr(svr.llm_replay, "invoke_agent",
                             lambda *a, **k: '{"AAA": {"decision": "HOLD"}}')
        ticks = svr.replay_check.build_tick_stream(frames)

        def fake_compute_risk_metrics(result, risk_free_rate=0.0):
            return {"sharpe": None, "sortino": None, "calmar": None, "max_drawdown_pct": None}

        monkeypatch.setattr(svr.replay_check, "compute_risk_metrics", fake_compute_risk_metrics)
        result = svr.run_variant_split_window("v", "s", frames, ticks, model="m")
        assert result["robust"] is False


class TestRunComparison:
    def test_builds_one_row_per_version(self, monkeypatch):
        frames = {"AAA": make_frame([(0, 55.0), (1, 55.0)])}
        monkeypatch.setattr(svr.replay_check, "fetch_history", lambda tickers: frames)
        monkeypatch.setattr(svr, "get_strategy_text_at_ref", lambda ref: f"strategy@{ref}")

        seen_texts = []

        def fake_run_variant(label, strategy_text, frames, ticks, model):
            seen_texts.append(strategy_text)
            return {"version": label, "sharpe": None, "first_half_sharpe": None,
                    "second_half_sharpe": None, "robust": False, "total_return_pct": 0.0,
                    "max_drawdown_pct": 0.0, "win_rate": 0.0, "n_trades": 0, "final_equity": 10000.0}

        monkeypatch.setattr(svr, "run_variant_split_window", fake_run_variant)

        comparison = svr.run_comparison(
            [("old", "sha1"), ("new", "sha2")], ["AAA"], lookback_days=30, model="m")

        assert "error" not in comparison
        assert [r["version"] for r in comparison["versions"]] == ["old", "new"]
        assert seen_texts == ["strategy@sha1", "strategy@sha2"]

    def test_no_history_returns_error(self, monkeypatch):
        monkeypatch.setattr(svr.replay_check, "fetch_history", lambda tickers: {})
        comparison = svr.run_comparison([("v", "ref")], ["AAA"], lookback_days=30, model="m")
        assert "error" in comparison

    def test_bad_git_ref_reported_per_row_not_fatal(self, monkeypatch):
        """One version with a bad ref shouldn't abort the whole comparison
        -- the other versions should still run."""
        frames = {"AAA": make_frame([(0, 55.0)])}
        monkeypatch.setattr(svr.replay_check, "fetch_history", lambda tickers: frames)

        def fake_get_text(ref):
            if ref == "bad-ref":
                raise RuntimeError("git show bad-ref:strategy.md failed: fatal: bad revision")
            return "good strategy text"

        monkeypatch.setattr(svr, "get_strategy_text_at_ref", fake_get_text)
        monkeypatch.setattr(svr, "run_variant_split_window",
                             lambda label, text, frames, ticks, model: {"version": label, "sharpe": 1.0})

        comparison = svr.run_comparison(
            [("broken", "bad-ref"), ("fine", "good-ref")], ["AAA"], lookback_days=30, model="m")

        assert "error" not in comparison
        rows = {r["version"]: r for r in comparison["versions"]}
        assert "error" in rows["broken"]
        assert rows["fine"]["sharpe"] == 1.0


class TestMainVersionsArg:
    def test_parses_label_colon_ref_pairs(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", [
            "strategy_version_replay.py", "--versions", "old:abc123", "new:HEAD",
            "--tickers", "AAA", "--json",
        ])
        seen = {}

        def fake_run_comparison(versions, tickers, lookback_days, model):
            seen["versions"] = versions
            return {"versions": []}

        monkeypatch.setattr(svr, "run_comparison", fake_run_comparison)
        rc = svr.main()
        assert rc == 0
        assert seen["versions"] == [("old", "abc123"), ("new", "HEAD")]

    def test_malformed_versions_entry_errors_before_running(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["strategy_version_replay.py", "--versions", "no-colon-here"])

        def fail_if_called(*a, **k):
            raise AssertionError("run_comparison should not run on malformed --versions")
        monkeypatch.setattr(svr, "run_comparison", fail_if_called)

        rc = svr.main()
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert "error" in out

    def test_no_versions_arg_uses_default_versions(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["strategy_version_replay.py", "--tickers", "AAA", "--json"])
        seen = {}

        def fake_run_comparison(versions, tickers, lookback_days, model):
            seen["versions"] = versions
            return {"versions": []}

        monkeypatch.setattr(svr, "run_comparison", fake_run_comparison)
        rc = svr.main()
        assert rc == 0
        assert seen["versions"] == svr.DEFAULT_VERSIONS
