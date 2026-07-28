#!/usr/bin/env python3
"""
Unit tests for scripts/discovery_screen.py — the pure ticker-list-in /
candidates-out screening logic extracted from discovery_scan.py on
2026-07-27, shared with discovery_daemon.py's continuous scanner. Same
fixtures/assertions as test_discovery_scan.py's old TestScreenCandidates,
now exercised directly against screen_tickers() with an explicit ticker
list instead of going through fetch_broad_universe's sampling.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import discovery_screen  # noqa: E402


def make_row(rsi=55.0, close=20.0, volume=100_000, volume_ma20=100_000, macd_hist=0.1):
    return pd.Series({
        "rsi_14": rsi, "close": close, "volume": volume,
        "volume_ma20": volume_ma20, "macd_hist": macd_hist,
    })


def make_frame(rsi=55.0, close=20.0, volume=100_000, volume_ma20=100_000):
    return pd.DataFrame([make_row(rsi, close, volume, volume_ma20).to_dict()])


class TestScreenTickers:
    def test_in_band_rsi_with_real_volume_passes(self, monkeypatch):
        frames = {"AAA": make_frame(rsi=55.0, volume=150_000, volume_ma20=100_000)}
        monkeypatch.setattr(discovery_screen.replay_check, "fetch_history", lambda tickers: frames)
        result = discovery_screen.screen_tickers(["AAA"], 1.0, 50.0)
        assert len(result) == 1
        assert result[0]["ticker"] == "AAA"
        assert result[0]["volume_ratio"] == pytest.approx(1.5)

    def test_rsi_outside_band_excluded(self, monkeypatch):
        frames = {"AAA": make_frame(rsi=80.0)}
        monkeypatch.setattr(discovery_screen.replay_check, "fetch_history", lambda tickers: frames)
        assert discovery_screen.screen_tickers(["AAA"], 1.0, 50.0) == []

    def test_below_average_volume_excluded(self, monkeypatch):
        frames = {"AAA": make_frame(rsi=55.0, volume=50_000, volume_ma20=100_000)}
        monkeypatch.setattr(discovery_screen.replay_check, "fetch_history", lambda tickers: frames)
        assert discovery_screen.screen_tickers(["AAA"], 1.0, 50.0) == []

    def test_missing_volume_ma20_does_not_exclude(self, monkeypatch):
        """No baseline to compare against yet (e.g. a freshly-listed name)
        -- fail open, don't reject just because the average is unknown."""
        frames = {"AAA": make_frame(rsi=55.0, volume=100_000, volume_ma20=float("nan"))}
        monkeypatch.setattr(discovery_screen.replay_check, "fetch_history", lambda tickers: frames)
        result = discovery_screen.screen_tickers(["AAA"], 1.0, 50.0)
        assert len(result) == 1
        assert result[0]["volume_ratio"] is None

    def test_price_band_filtering_applied_before_screening(self, monkeypatch):
        frames = {
            "CHEAP": make_frame(rsi=55.0, close=10.0),
            "EXPENSIVE": make_frame(rsi=55.0, close=500.0),
        }
        monkeypatch.setattr(discovery_screen.replay_check, "fetch_history", lambda tickers: frames)
        result = discovery_screen.screen_tickers(["CHEAP", "EXPENSIVE"], 1.0, 50.0)
        assert [c["ticker"] for c in result] == ["CHEAP"]

    def test_sorted_by_volume_ratio_descending(self, monkeypatch):
        frames = {
            "LOW": make_frame(rsi=55.0, volume=110_000, volume_ma20=100_000),
            "HIGH": make_frame(rsi=55.0, volume=300_000, volume_ma20=100_000),
        }
        monkeypatch.setattr(discovery_screen.replay_check, "fetch_history", lambda tickers: frames)
        result = discovery_screen.screen_tickers(["LOW", "HIGH"], 1.0, 50.0)
        assert [c["ticker"] for c in result] == ["HIGH", "LOW"]
