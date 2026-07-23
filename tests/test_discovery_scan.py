#!/usr/bin/env python3
"""
Unit tests for scripts/discovery_scan.py — pure logic (screening filter,
price-band sourcing, news confirmation, file writing). No network —
fetch_broad_universe/fetch_history/fetch_alpaca_news are all mocked,
matching every other script's no-network test convention this session.
"""
import datetime
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(REPO_ROOT))

import discovery_scan  # noqa: E402
import bankroll  # noqa: E402


def make_row(rsi=55.0, close=20.0, volume=100_000, volume_ma20=100_000, macd_hist=0.1):
    return pd.Series({
        "rsi_14": rsi, "close": close, "volume": volume,
        "volume_ma20": volume_ma20, "macd_hist": macd_hist,
    })


def make_frame(rsi=55.0, close=20.0, volume=100_000, volume_ma20=100_000):
    return pd.DataFrame([make_row(rsi, close, volume, volume_ma20).to_dict()])


class TestGetUniversePriceBand:
    def test_reads_min_price_from_params_and_max_from_bankroll(self, tmp_path, monkeypatch):
        params_path = tmp_path / "params.json"
        params_path.write_text(json.dumps({"universe": {"min_price": 2.0}}))
        bankroll_file = tmp_path / "bankroll.md"
        bankroll_file.write_text("Ceiling: $51.00\n")
        monkeypatch.setattr(discovery_scan, "PARAMS_PATH", params_path)
        monkeypatch.setattr(bankroll, "BANKROLL_FILE", bankroll_file)
        min_price, max_price = discovery_scan.get_universe_price_band()
        assert min_price == 2.0
        assert max_price == 50.0  # ceiling 51 -> first tier, unchanged

    def test_higher_ceiling_widens_universe(self, tmp_path, monkeypatch):
        params_path = tmp_path / "params.json"
        params_path.write_text(json.dumps({"universe": {"min_price": 1.0}}))
        bankroll_file = tmp_path / "bankroll.md"
        bankroll_file.write_text("Ceiling: $500.00\n")
        monkeypatch.setattr(discovery_scan, "PARAMS_PATH", params_path)
        monkeypatch.setattr(bankroll, "BANKROLL_FILE", bankroll_file)
        _, max_price = discovery_scan.get_universe_price_band()
        assert max_price == 150.0


class TestScreenCandidates:
    def test_in_band_rsi_with_real_volume_passes(self, monkeypatch):
        frames = {"AAA": make_frame(rsi=55.0, volume=150_000, volume_ma20=100_000)}
        monkeypatch.setattr(discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(discovery_scan.universe_scan, "fetch_broad_universe", lambda **k: ["AAA"])
        monkeypatch.setattr(discovery_scan.replay_check, "fetch_history", lambda tickers: frames)
        result = discovery_scan.screen_candidates()
        assert len(result) == 1
        assert result[0]["ticker"] == "AAA"
        assert result[0]["volume_ratio"] == pytest.approx(1.5)

    def test_rsi_outside_band_excluded(self, monkeypatch):
        frames = {"AAA": make_frame(rsi=80.0)}
        monkeypatch.setattr(discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(discovery_scan.universe_scan, "fetch_broad_universe", lambda **k: ["AAA"])
        monkeypatch.setattr(discovery_scan.replay_check, "fetch_history", lambda tickers: frames)
        assert discovery_scan.screen_candidates() == []

    def test_below_average_volume_excluded(self, monkeypatch):
        frames = {"AAA": make_frame(rsi=55.0, volume=50_000, volume_ma20=100_000)}
        monkeypatch.setattr(discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(discovery_scan.universe_scan, "fetch_broad_universe", lambda **k: ["AAA"])
        monkeypatch.setattr(discovery_scan.replay_check, "fetch_history", lambda tickers: frames)
        assert discovery_scan.screen_candidates() == []

    def test_missing_volume_ma20_does_not_exclude(self, monkeypatch):
        """No baseline to compare against yet (e.g. a freshly-listed name)
        -- fail open, don't reject just because the average is unknown."""
        frames = {"AAA": make_frame(rsi=55.0, volume=100_000, volume_ma20=float("nan"))}
        monkeypatch.setattr(discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(discovery_scan.universe_scan, "fetch_broad_universe", lambda **k: ["AAA"])
        monkeypatch.setattr(discovery_scan.replay_check, "fetch_history", lambda tickers: frames)
        result = discovery_scan.screen_candidates()
        assert len(result) == 1
        assert result[0]["volume_ratio"] is None

    def test_price_band_filtering_applied_before_screening(self, monkeypatch):
        frames = {
            "CHEAP": make_frame(rsi=55.0, close=10.0),
            "EXPENSIVE": make_frame(rsi=55.0, close=500.0),
        }
        monkeypatch.setattr(discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(discovery_scan.universe_scan, "fetch_broad_universe",
                             lambda **k: ["CHEAP", "EXPENSIVE"])
        monkeypatch.setattr(discovery_scan.replay_check, "fetch_history", lambda tickers: frames)
        result = discovery_scan.screen_candidates()
        assert [c["ticker"] for c in result] == ["CHEAP"]

    def test_sorted_by_volume_ratio_descending(self, monkeypatch):
        frames = {
            "LOW": make_frame(rsi=55.0, volume=110_000, volume_ma20=100_000),
            "HIGH": make_frame(rsi=55.0, volume=300_000, volume_ma20=100_000),
        }
        monkeypatch.setattr(discovery_scan, "get_universe_price_band", lambda: (1.0, 50.0))
        monkeypatch.setattr(discovery_scan.universe_scan, "fetch_broad_universe",
                             lambda **k: ["LOW", "HIGH"])
        monkeypatch.setattr(discovery_scan.replay_check, "fetch_history", lambda tickers: frames)
        result = discovery_scan.screen_candidates()
        assert [c["ticker"] for c in result] == ["HIGH", "LOW"]


class TestConfirmWithNews:
    def test_candidate_with_news_gets_sentiment(self, monkeypatch):
        candidates = [{"ticker": "AAA", "price": 10.0, "rsi": 55.0, "volume_ratio": 1.2, "macd_hist": 0.1}]
        monkeypatch.setattr(discovery_scan.news_collector, "fetch_alpaca_news",
                             lambda tickers, limit=5: [{"title": "AAA surges", "summary": "great news"}])
        monkeypatch.setattr(discovery_scan.news_collector, "score_sentiment", lambda text: 0.5)
        result = discovery_scan.confirm_with_news(candidates)
        assert result[0]["sentiment"] == 0.5
        assert result[0]["news_headline"] == "AAA surges"

    def test_candidate_with_no_news_reports_honestly(self, monkeypatch):
        candidates = [{"ticker": "ZZZ", "price": 10.0, "rsi": 55.0, "volume_ratio": 1.2, "macd_hist": 0.1}]
        monkeypatch.setattr(discovery_scan.news_collector, "fetch_alpaca_news", lambda tickers, limit=5: [])
        result = discovery_scan.confirm_with_news(candidates)
        assert result[0]["sentiment"] is None
        assert result[0]["news_headline"] is None

    def test_respects_top_n_limit(self, monkeypatch):
        candidates = [
            {"ticker": f"T{i}", "price": 10.0, "rsi": 55.0, "volume_ratio": 1.0, "macd_hist": 0.1}
            for i in range(5)
        ]
        monkeypatch.setattr(discovery_scan.news_collector, "fetch_alpaca_news", lambda tickers, limit=5: [])
        result = discovery_scan.confirm_with_news(candidates, top_n=2)
        assert len(result) == 2


class TestWriteDiscoveriesFile:
    def test_writes_expected_header_format(self, tmp_path):
        candidates = [{"ticker": "AAA", "price": 12.34, "rsi": 55.0, "volume_ratio": 1.5,
                        "news_headline": "AAA news", "sentiment": 0.3}]
        path = tmp_path / "2026-07-23.md"
        discovery_scan.write_discoveries_file(candidates, 1.0, 50.0, path=path)
        text = path.read_text()
        assert "## AAA — $12.34" in text  # exact format merge_discoveries.py's regex requires

    def test_no_news_candidate_says_so(self, tmp_path):
        candidates = [{"ticker": "ZZZ", "price": 5.0, "rsi": 55.0, "volume_ratio": None,
                        "news_headline": None, "sentiment": None}]
        path = tmp_path / "2026-07-23.md"
        discovery_scan.write_discoveries_file(candidates, 1.0, 50.0, path=path)
        text = path.read_text()
        assert "technical signal only" in text

    def test_merge_discoveries_can_parse_output(self, tmp_path):
        """End-to-end format compatibility check against the real
        downstream consumer's regex, not just a string match here."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        import merge_discoveries
        candidates = [{"ticker": "AAA", "price": 12.34, "rsi": 55.0, "volume_ratio": 1.5,
                        "news_headline": None, "sentiment": None}]
        path = tmp_path / "2026-07-23.md"
        discovery_scan.write_discoveries_file(candidates, 1.0, 50.0, path=path)
        assert merge_discoveries.extract_candidates(path.read_text()) == ["AAA"]
