#!/usr/bin/env python3
"""
Unit tests for scripts/llm_replay.py — pure logic (prompt construction,
decision parsing, per-day memoization). No real agent invocation —
invoke_agent() is monkeypatched everywhere, matching every other script's
no-network test convention this session.
"""
import datetime
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, "/home/openclaw/projects/paper-trading-rebuild")

import llm_replay  # noqa: E402
from src.replay import TraderDecision  # noqa: E402


class TestBuildReplayPrompt:
    def test_includes_safety_guard_header(self):
        prompt = llm_replay.build_replay_prompt("2026-01-01", {}, {"cash": 1000.0, "positions": {}}, "strategy")
        assert "NOT a live tick" in prompt
        assert "Do not call any tools" in prompt
        assert "Do not place any trades" in prompt

    def test_includes_strategy_text_verbatim(self):
        prompt = llm_replay.build_replay_prompt(
            "2026-01-01", {}, {"cash": 1000.0, "positions": {}}, "UNIQUE STRATEGY MARKER 12345")
        assert "UNIQUE STRATEGY MARKER 12345" in prompt

    def test_includes_portfolio_positions(self):
        state = {"cash": 500.0, "positions": {"NVDA": {"qty": 3, "entry_price": 200.0}}}
        prompt = llm_replay.build_replay_prompt("2026-01-01", {}, state, "s")
        assert "NVDA" in prompt and "3 shares" in prompt and "$200.00" in prompt

    def test_no_positions_says_so(self):
        prompt = llm_replay.build_replay_prompt("2026-01-01", {}, {"cash": 500.0, "positions": {}}, "s")
        assert "no open positions" in prompt

    def test_includes_ticker_snapshot_data(self):
        snapshot = {"GME": {"close": 22.5, "rsi_14": 60.0, "macd_hist": 0.05,
                             "macd_line": 0.1, "macd_signal": 0.05, "ma20": 21.0, "ma50": 20.0}}
        prompt = llm_replay.build_replay_prompt("2026-01-01", snapshot, {"cash": 1.0, "positions": {}}, "s")
        assert "GME" in prompt and "rsi14=60.0" in prompt


class TestParseDecisions:
    def test_valid_json_parses_correctly(self):
        text = '{"NVDA": {"decision": "BUY", "shares": 2, "conviction": 0.7, "rationale": "momentum"}}'
        result = llm_replay.parse_decisions(text, ["NVDA"])
        assert result["NVDA"].decision == "BUY"
        assert result["NVDA"].shares == 2
        assert result["NVDA"].conviction == 0.7

    def test_json_embedded_in_prose_still_parses(self):
        text = 'Here is my decision:\n{"NVDA": {"decision": "SELL", "shares": 1}}\nHope that helps!'
        result = llm_replay.parse_decisions(text, ["NVDA"])
        assert result["NVDA"].decision == "SELL"

    def test_none_text_defaults_all_to_hold(self):
        result = llm_replay.parse_decisions(None, ["NVDA", "GME"])
        assert result["NVDA"].decision == "HOLD"
        assert result["GME"].decision == "HOLD"
        assert "defaulted to HOLD" in result["NVDA"].rationale

    def test_unparseable_text_defaults_to_hold(self):
        result = llm_replay.parse_decisions("not json at all", ["NVDA"])
        assert result["NVDA"].decision == "HOLD"

    def test_missing_ticker_in_response_defaults_to_hold(self):
        text = '{"NVDA": {"decision": "BUY", "shares": 1}}'
        result = llm_replay.parse_decisions(text, ["NVDA", "GME"])
        assert result["NVDA"].decision == "BUY"
        assert result["GME"].decision == "HOLD"

    def test_invalid_decision_string_defaults_to_hold(self):
        text = '{"NVDA": {"decision": "MAYBE", "shares": 1}}'
        result = llm_replay.parse_decisions(text, ["NVDA"])
        assert result["NVDA"].decision == "HOLD"

    def test_missing_conviction_and_shares_default_sanely(self):
        text = '{"NVDA": {"decision": "HOLD"}}'
        result = llm_replay.parse_decisions(text, ["NVDA"])
        assert result["NVDA"].conviction == 0.0
        assert result["NVDA"].shares == 0


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


class TestMakeLlmTrader:
    def test_calls_invoke_agent_once_per_day_not_per_ticker(self, monkeypatch):
        """The core cost-control property: replay_trader() calls the
        trader callback once per (ticker, tick) — for 2 tickers on the
        same day that's 2 calls, but should only trigger 1 real LLM call."""
        frames = {
            "AAA": make_frame([(0, 55.0)]),
            "BBB": make_frame([(0, 50.0)]),
        }
        call_count = []

        def fake_invoke(prompt, model=None, timeout=None):
            call_count.append(1)
            return '{"AAA": {"decision": "HOLD"}, "BBB": {"decision": "HOLD"}}'

        monkeypatch.setattr(llm_replay, "invoke_agent", fake_invoke)
        trader = llm_replay.make_llm_trader(frames, "strategy text")

        ticks = llm_replay.replay_check.build_tick_stream(frames)
        portfolio = SimpleNamespace(cash=10000.0, positions={})
        for tick in ticks:
            trader(tick, portfolio)

        assert len(call_count) == 1

    def test_calls_invoke_agent_again_on_new_day(self, monkeypatch):
        frames = {"AAA": make_frame([(0, 55.0), (1, 50.0)])}
        call_count = []

        def fake_invoke(prompt, model=None, timeout=None):
            call_count.append(1)
            return '{"AAA": {"decision": "HOLD"}}'

        monkeypatch.setattr(llm_replay, "invoke_agent", fake_invoke)
        trader = llm_replay.make_llm_trader(frames, "strategy text")
        ticks = llm_replay.replay_check.build_tick_stream(frames)
        portfolio = SimpleNamespace(cash=10000.0, positions={})
        for tick in ticks:
            trader(tick, portfolio)

        assert len(call_count) == 2

    def test_returns_hold_for_ticker_missing_from_snapshot(self, monkeypatch):
        """Defensive: if lookup somehow can't find a ticker's row for this
        exact tick, fail open to HOLD rather than crash."""
        frames = {"AAA": make_frame([(0, 55.0)])}
        monkeypatch.setattr(llm_replay, "invoke_agent", lambda *a, **k: '{"AAA": {"decision": "BUY"}}')
        trader = llm_replay.make_llm_trader(frames, "strategy text")

        fake_tick = SimpleNamespace(timestamp=datetime.datetime(2026, 1, 1), ticker="ZZZZ", rsi=50.0)
        portfolio = SimpleNamespace(cash=10000.0, positions={})
        decision = trader(fake_tick, portfolio)
        assert decision.decision == "HOLD"

    def test_passes_model_through_to_invoke_agent(self, monkeypatch):
        frames = {"AAA": make_frame([(0, 55.0)])}
        seen_models = []

        def fake_invoke(prompt, model=None, timeout=None):
            seen_models.append(model)
            return '{"AAA": {"decision": "HOLD"}}'

        monkeypatch.setattr(llm_replay, "invoke_agent", fake_invoke)
        trader = llm_replay.make_llm_trader(frames, "strategy text", model="custom/model")
        ticks = llm_replay.replay_check.build_tick_stream(frames)
        portfolio = SimpleNamespace(cash=10000.0, positions={})
        trader(ticks[0], portfolio)
        assert seen_models == ["custom/model"]


class TestRunLlmReplay:
    def test_missing_strategy_file_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(llm_replay, "STRATEGY_PATH", tmp_path / "does_not_exist.md")
        result = llm_replay.run_llm_replay(["AAA"])
        assert "error" in result

    def test_no_history_returns_error(self, monkeypatch, tmp_path):
        strategy_file = tmp_path / "strategy.md"
        strategy_file.write_text("# strategy")
        monkeypatch.setattr(llm_replay, "STRATEGY_PATH", strategy_file)
        monkeypatch.setattr(llm_replay.replay_check, "fetch_history", lambda tickers: {})
        result = llm_replay.run_llm_replay(["AAA"])
        assert "error" in result
