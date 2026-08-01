#!/usr/bin/env python3
"""
Unit tests for scripts/universe_scan.py's pure logic (sampling, price-band
filtering, ranking, first-signal-date detection). No network, no Alpaca
creds — Alpaca calls (fetch_broad_universe's TradingClient.get_all_assets,
replay_check.fetch_history) are exercised manually, not here.
"""
import datetime
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, "/home/openclaw/projects/paper-trading-rebuild")

import universe_scan  # noqa: E402


def make_bar_df(closes, rsi_values=None, start=None):
    """Minimal frame matching fetch_history's output shape (only the
    columns universe_scan.py actually reads)."""
    start = start or datetime.datetime(2026, 1, 1)
    n = len(closes)
    timestamps = [start + datetime.timedelta(days=i) for i in range(n)]
    rsi_values = rsi_values if rsi_values is not None else [50.0] * n
    return pd.DataFrame({
        "timestamp": timestamps,
        "close": closes,
        "rsi_14": rsi_values,
    })


class TestIsCommonStock:
    """2026-08-01: the filter was PLAIN_TICKER_RE (^[A-Z]{1,5}$) alone,
    whose docstring claimed to exclude preferred shares/warrants/class
    shares. It didn't -- LFMDP/VLYPN/BHFAL are all 1-5 uppercase letters
    too, and they filled the discovery pool's promotion queue. Alpaca has
    no security-type field (asset_class is US_EQUITY for all of these), so
    the name is what decides."""

    @pytest.mark.parametrize("symbol,name", [
        ("LFMDP", "LifeMD, Inc. 8.875% Series A Cumulative Perpetual Preferred Stock"),
        ("VLYPN", "Valley National Bancorp Preferred Series B"),
        ("IONQW", "IonQ, Inc. Warrants"),
        ("RDACU", "Rising Dragon Acquisition Corp. Units"),
        ("ABCR", "Some Acquisition Corp Rights"),
        ("BHFAL", "Brighthouse Financial, Inc. 6.25% Junior Subordinated Debentures"),
        ("XYZD", "Some Bank Depositary Shares"),
    ])
    def test_excludes_non_common_securities(self, symbol, name):
        assert not universe_scan.is_common_stock(symbol, name)

    @pytest.mark.parametrize("symbol,name", [
        ("CXSE", "WisdomTree China ex-State-Owned Enterprises Fund"),
        ("GFGF", "Guinness Atkinson Global Innovators ETF"),
        ("SPY", "SPDR S&P 500 ETF Trust"),
        ("VOO", "Vanguard S&P 500 Index Fund ETF Shares"),
        ("AMJ", "JPMorgan Alerian MLP Index ETN"),
    ])
    def test_excludes_funds(self, symbol, name):
        assert not universe_scan.is_common_stock(symbol, name)

    @pytest.mark.parametrize("symbol,name", [
        ("ZEO", "Zeo Energy Corp."),
        ("AAPL", "Apple Inc."),
        ("SOFI", "SoFi Technologies, Inc."),
        # "Trust" deliberately isn't a fund keyword -- these are operating
        # companies and REITs, not ETFs.
        ("NTRS", "Northern Trust Corporation"),
        ("PEI", "Pennsylvania Real Estate Investment Trust"),
    ])
    def test_keeps_operating_companies(self, symbol, name):
        assert universe_scan.is_common_stock(symbol, name)

    def test_rejects_malformed_symbols_regardless_of_name(self):
        assert not universe_scan.is_common_stock("WRB.PRF", "W. R. Berkley Corporation")
        assert not universe_scan.is_common_stock("TOOLONG", "Some Company Inc.")

    def test_falls_back_to_symbol_shape_without_a_name(self):
        """Only reachable when Alpaca returns no name -- coarse (it also
        catches genuine class shares like GOOGL) but better than nothing."""
        assert not universe_scan.is_common_stock("LFMDP")
        assert not universe_scan.is_common_stock("IONQW")
        assert universe_scan.is_common_stock("SOFI")
        assert universe_scan.is_common_stock("ZEO")


class TestFetchBroadUniverse:
    def _mock_asset(self, symbol, tradable=True, exchange="NASDAQ", name=None):
        return SimpleNamespace(symbol=symbol, tradable=tradable, exchange=exchange, name=name)

    def _patch_client(self, monkeypatch, assets):
        mock_client = MagicMock()
        mock_client.get_all_assets.return_value = assets
        monkeypatch.setattr(universe_scan, "TradingClient", lambda *a, **k: mock_client)
        monkeypatch.setenv("ALPACA_STONKS_KEY", "fake")
        monkeypatch.setenv("ALPACA_STONKS_SECRET", "fake")

    def _symbol_pool(self, n):
        """n plain A-Z ticker-shaped symbols (PLAIN_TICKER_RE requires
        letters only, 1-5 chars — no digits, matching real tickers)."""
        import itertools
        import string
        pool = []
        for letters in itertools.product(string.ascii_uppercase, repeat=3):
            pool.append("".join(letters))
            if len(pool) >= n:
                break
        return pool

    def test_same_seed_gives_same_sample(self, monkeypatch):
        from alpaca.trading.enums import AssetExchange
        symbols = self._symbol_pool(50)
        assets = [self._mock_asset(s, exchange=AssetExchange.NASDAQ) for s in symbols]
        self._patch_client(monkeypatch, assets)
        first = universe_scan.fetch_broad_universe(sample_size=10, seed="fixed-seed")
        second = universe_scan.fetch_broad_universe(sample_size=10, seed="fixed-seed")
        assert first == second
        assert len(first) == 10

    def test_different_seed_gives_different_sample(self, monkeypatch):
        from alpaca.trading.enums import AssetExchange
        symbols = self._symbol_pool(50)
        assets = [self._mock_asset(s, exchange=AssetExchange.NASDAQ) for s in symbols]
        self._patch_client(monkeypatch, assets)
        first = universe_scan.fetch_broad_universe(sample_size=10, seed="seed-a")
        second = universe_scan.fetch_broad_universe(sample_size=10, seed="seed-b")
        assert first != second

    def test_excludes_non_tradable_and_wrong_exchange_and_malformed_symbols(self, monkeypatch):
        from alpaca.trading.enums import AssetExchange
        assets = [
            self._mock_asset("GOOD", exchange=AssetExchange.NASDAQ),
            self._mock_asset("NOTRADE", tradable=False, exchange=AssetExchange.NASDAQ),
            self._mock_asset("OTCJUNK", exchange=AssetExchange.OTC),
            self._mock_asset("WRB.PRF", exchange=AssetExchange.NYSE),
            self._mock_asset("IONQ.WS", exchange=AssetExchange.NYSE),
        ]
        self._patch_client(monkeypatch, assets)
        result = universe_scan.fetch_broad_universe(sample_size=10, seed="s")
        assert result == ["GOOD"]

    def test_excludes_preferred_and_funds_by_asset_name(self, monkeypatch):
        """The live bug: these are all 1-5 uppercase letters, so the old
        plain-ticker regex passed every one of them straight into the
        discovery pool."""
        from alpaca.trading.enums import AssetExchange
        assets = [
            self._mock_asset("ZEO", exchange=AssetExchange.NASDAQ, name="Zeo Energy Corp."),
            self._mock_asset("LFMDP", exchange=AssetExchange.NASDAQ,
                              name="LifeMD, Inc. Series A Cumulative Perpetual Preferred Stock"),
            self._mock_asset("CXSE", exchange=AssetExchange.NASDAQ,
                              name="WisdomTree China ex-State-Owned Enterprises Fund"),
            self._mock_asset("IONQW", exchange=AssetExchange.NYSE, name="IonQ, Inc. Warrants"),
        ]
        self._patch_client(monkeypatch, assets)
        assert universe_scan.fetch_broad_universe(sample_size=10, seed="s") == ["ZEO"]

    def test_sample_size_larger_than_universe_returns_all(self, monkeypatch):
        from alpaca.trading.enums import AssetExchange
        symbols = self._symbol_pool(5)
        assets = [self._mock_asset(s, exchange=AssetExchange.NASDAQ) for s in symbols]
        self._patch_client(monkeypatch, assets)
        result = universe_scan.fetch_broad_universe(sample_size=200, seed="s")
        assert sorted(result) == sorted(symbols)

    def test_sample_size_none_returns_full_sorted_list(self, monkeypatch):
        """2026-07-27: discovery_daemon.py needs the whole tradable universe
        (to build its own persisted rotation cursor), not a capped sample."""
        from alpaca.trading.enums import AssetExchange
        symbols = self._symbol_pool(30)
        assets = [self._mock_asset(s, exchange=AssetExchange.NASDAQ) for s in symbols]
        self._patch_client(monkeypatch, assets)
        result = universe_scan.fetch_broad_universe(sample_size=None)
        assert result == sorted(symbols)
        assert len(result) == 30


class TestFilterByPriceBand:
    def test_keeps_ticker_in_band(self):
        frames = {"A": make_bar_df([10, 20, 30])}
        result = universe_scan.filter_by_price_band(frames, min_price=1.0, max_price=50.0)
        assert "A" in result

    def test_drops_ticker_always_above_band(self):
        frames = {"A": make_bar_df([100, 200, 300])}
        result = universe_scan.filter_by_price_band(frames, min_price=1.0, max_price=50.0)
        assert "A" not in result

    def test_keeps_ticker_that_touches_band_briefly(self):
        frames = {"A": make_bar_df([100, 45, 200])}
        result = universe_scan.filter_by_price_band(frames, min_price=1.0, max_price=50.0)
        assert "A" in result


class TestFirstSignalDate:
    def test_finds_first_in_band_date(self):
        df = make_bar_df([10, 10, 10], rsi_values=[70, 50, 40])
        signal = universe_scan.first_signal_date(df)
        assert signal == df["timestamp"].iloc[1]

    def test_returns_none_when_never_in_band(self):
        df = make_bar_df([10, 10, 10], rsi_values=[80, 85, 90])
        assert universe_scan.first_signal_date(df) is None

    def test_boundary_values_excluded(self):
        """45 and 65 themselves are NOT in-band (strict inequality, matches
        make_trader's `45 < tick.rsi < 65`)."""
        df = make_bar_df([10, 10, 10], rsi_values=[45, 65, 50])
        signal = universe_scan.first_signal_date(df)
        assert signal == df["timestamp"].iloc[2]


class TestRankTickers:
    def _fake_result(self, n_trades, sharpe, total_return_pct, entry_time=None):
        return SimpleNamespace(
            n_ticks=10, n_decisions=n_trades, trades=[
                SimpleNamespace(entry_time=entry_time or datetime.datetime(2026, 1, 1))
                for _ in range(n_trades)
            ],
            win_rate=0.5, total_return_pct=total_return_pct, total_pnl=1.0,
            final_equity=10001.0, timestamps=[], equity_curve=np.array([]),
        )

    def test_excludes_already_known_tickers(self, monkeypatch):
        frames = {"KNOWN": make_bar_df([10] * 5), "NEW": make_bar_df([10] * 5)}
        monkeypatch.setattr(universe_scan.replay_check, "build_tick_stream", lambda f: [1])
        monkeypatch.setattr(universe_scan.replay_check, "STRATEGY_BUILDERS",
                             {"v1.0": lambda frames: (lambda tick, pf: None)})
        monkeypatch.setattr(universe_scan, "replay_trader",
                             lambda *a, **k: self._fake_result(3, 1.0, 2.0))
        ranked = universe_scan.rank_tickers(frames, exclude={"KNOWN"})
        tickers = [r["ticker"] for r in ranked]
        assert "KNOWN" not in tickers
        assert "NEW" in tickers

    def test_filters_out_thin_sample_below_min_trades(self, monkeypatch):
        frames = {"THIN": make_bar_df([10] * 5), "SOLID": make_bar_df([10] * 5)}
        monkeypatch.setattr(universe_scan.replay_check, "build_tick_stream", lambda f: [1])
        monkeypatch.setattr(universe_scan.replay_check, "STRATEGY_BUILDERS",
                             {"v1.0": lambda frames: (lambda tick, pf: None)})

        def fake_replay(*a, **k):
            # first call THIN (1 trade, huge sharpe), second SOLID (3 trades)
            return fake_replay.calls.pop(0)
        fake_replay.calls = [self._fake_result(1, 5.0, 0.1), self._fake_result(3, 1.0, 2.0)]
        monkeypatch.setattr(universe_scan, "replay_trader", fake_replay)

        ranked = universe_scan.rank_tickers(frames, exclude=set())
        tickers = [r["ticker"] for r in ranked]
        assert "THIN" not in tickers
        assert "SOLID" in tickers

    def test_sorts_by_sharpe_falling_back_to_return_pct(self, monkeypatch):
        frames = {"HIGH_SHARPE": make_bar_df([10] * 5), "NO_SHARPE": make_bar_df([10] * 5)}
        monkeypatch.setattr(universe_scan.replay_check, "build_tick_stream", lambda f: [1])
        monkeypatch.setattr(universe_scan.replay_check, "STRATEGY_BUILDERS",
                             {"v1.0": lambda frames: (lambda tick, pf: None)})

        results = {
            "HIGH_SHARPE": self._fake_result(3, 2.0, 5.0),
            "NO_SHARPE": self._fake_result(3, 0.0, 1.0),  # sharpe None-ish via summarize path
        }

        def fake_summarize(result, label):
            base = {"variant": label, "n_ticks": 10, "n_decisions": 3, "n_trades": 3,
                    "win_rate": 0.5, "total_return_pct": result.total_return_pct,
                    "total_pnl": 1.0, "final_equity": 10001.0}
            if result.total_return_pct == 5.0:
                base["sharpe"] = 2.0
            else:
                base["sharpe"] = None
            base.update({"sortino": None, "calmar": None, "max_drawdown_pct": 0.0})
            return base

        call_order = iter(["HIGH_SHARPE", "NO_SHARPE"])

        def fake_replay(ticks, trader_fn, **kwargs):
            return results[next(call_order)]

        monkeypatch.setattr(universe_scan, "replay_trader", fake_replay)
        monkeypatch.setattr(universe_scan.replay_check, "summarize", fake_summarize)

        ranked = universe_scan.rank_tickers(frames, exclude=set())
        # Sharpe present (1, x) always outranks sharpe=None (0, return_pct) in the tuple sort.
        assert ranked[0]["ticker"] == "HIGH_SHARPE"
