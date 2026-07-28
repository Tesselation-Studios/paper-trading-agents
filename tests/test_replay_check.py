#!/usr/bin/env python3
"""Unit tests for scripts/replay_check.py's v1.6/v1.7 additions —
scale-into-winners and the max_positions cap. No network/Alpaca —
synthetic frames and a hand-built Portfolio/Tick, same pattern
paper-trading-rebuild's own replay.py test helpers use.

make_trader() only reads `frames` to build a read-only lookup keyed by
(ticker, timestamp) — a single synthetic row per ticker is enough to drive
the decision logic under test without needing real market data.
"""
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, "/home/openclaw/projects/paper-trading-rebuild")

import replay_check  # noqa: E402
import trader_db  # noqa: E402
from src.replay import Tick, Portfolio, Position  # noqa: E402

TS = datetime(2026, 6, 1, 9, 30, 0)


class TestLoadLiveUniverse:
    """Migrated 2026-07-28 from globbing positions/*.md + regex-parsing
    watchlist.md to querying trader_db.py directly."""

    def test_unions_open_positions_and_candidates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.upsert_watchlist_candidate(conn, ticker="BBB")
        conn.close()

        assert replay_check.load_live_universe() == ["AAA", "BBB"]

    def test_closed_positions_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        conn = trader_db.get_conn(tmp_path / "trader.db")
        trader_db.upsert_position(conn, ticker="AAA", shares=1.0, entry_price=10.0, entry_time="t1")
        trader_db.close_position(conn, ticker="AAA", closed_at="t2", close_reason="exit", realized_pnl=1.0, realized_return_pct=1.0)
        conn.close()

        assert replay_check.load_live_universe() == list(replay_check.FALLBACK_TICKERS)

    def test_falls_back_when_both_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trader_db, "DB_PATH", tmp_path / "trader.db")
        assert replay_check.load_live_universe() == list(replay_check.FALLBACK_TICKERS)


def make_frames(ticker, rsi, macd_hist, vol_20d=0.01):
    row = {
        "timestamp": TS, "rsi_14": rsi, "macd_line": 0.1, "macd_hist": macd_hist,
        "macd_signal": 0.05, "vol_20d": vol_20d, "ma20": None, "ma50": None,
    }
    return {ticker: pd.DataFrame([row])}


class TestScaleIntoWinners:
    def _held_tick_portfolio(self, entry_price, close_price, entry_time=TS, rsi=55.0):
        pos = Position(ticker="XYZ", shares=10, entry_price=entry_price, entry_time=entry_time,
                        current_price=close_price)
        portfolio = Portfolio(cash=5000.0, positions={"XYZ": pos})
        tick = Tick(timestamp=TS, ticker="XYZ", open=close_price, high=close_price,
                     low=close_price, close=close_price, volume=100_000, rsi=rsi)
        return tick, portfolio

    def test_disabled_by_default_stays_hold(self):
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.1")  # scale_into_winners defaults False
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.5)
        decision = trader(tick, portfolio)
        assert decision.decision == "HOLD"

    def test_real_winner_in_band_triggers_scale_in(self):
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True)
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.5)  # +5% pnl
        decision = trader(tick, portfolio)
        assert decision.decision == "BUY"
        assert decision.conviction == replay_check.ENTRY_CONVICTION  # clears require_conviction gate
        assert decision.shares > 0  # explicit shares carry the sizing, not conviction
        assert "scale into winner" in decision.rationale

    def test_pnl_below_threshold_does_not_scale_in(self):
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True)
        # +1% pnl — real gain but below SCALE_IN_MIN_PNL_PCT (3%), not a real winner yet
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.10)
        decision = trader(tick, portfolio)
        assert decision.decision == "HOLD"

    def test_no_cooldown_v17_reconsiders_every_tick(self):
        """v1.7: reconsidered every tick it still qualifies — no pacing,
        unlike the old (removed) 5-day-gap cooldown."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True)
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.5)

        first = trader(tick, portfolio)
        second = trader(tick, portfolio)
        assert first.decision == "BUY"
        assert second.decision == "BUY"  # no cooldown blocking the very next call

    def test_out_of_rsi_band_does_not_scale_in(self):
        frames = make_frames("XYZ", rsi=80.0, macd_hist=0.5)  # overbought, out of 45-65 band
        trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True)
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.5, rsi=80.0)
        decision = trader(tick, portfolio)
        assert decision.decision == "HOLD"

    def test_choppy_regime_blocks_v11_scale_in(self):
        """v1.1's regime-gating applies to scale-ins too, same as fresh entries."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5, vol_20d=0.05)  # above CHOPPY_VOL_THRESHOLD
        trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True)
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.5)
        decision = trader(tick, portfolio)
        assert decision.decision == "HOLD"

    def test_bigger_winner_gets_bigger_add(self):
        """v1.7: size scales with magnitude, not a fixed increment. Both
        magnitudes stay under PROFIT_TARGET_PCT (12%) so the exit check
        doesn't fire first, and under the pnl where SCALE_IN_MAX_MULTIPLE
        (3x at 3x SCALE_IN_MIN_PNL_PCT = 9%) caps the multiple, which would
        otherwise mask the size difference."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True)
        small_tick, small_winner_portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.35)  # +3.5%
        big_tick, big_winner_portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.6)  # +6%

        small_add = trader(small_tick, small_winner_portfolio)
        big_add = trader(big_tick, big_winner_portfolio)
        assert small_add.decision == "BUY"
        assert big_add.decision == "BUY"
        assert big_add.shares > small_add.shares

    def test_size_multiple_caps_at_scale_in_max_multiple(self):
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True)
        # +11% pnl — still under the 12% profit-target exit, but far beyond
        # what SCALE_IN_MAX_MULTIPLE should let the size multiple grow past.
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=11.1)
        decision = trader(tick, portfolio)
        assert decision.decision == "BUY"
        expected_max_shares = int(
            (portfolio.total_equity * replay_check.MAX_POSITION_PCT * replay_check.ENTRY_CONVICTION
             * replay_check.SCALE_IN_SIZE_FACTOR * replay_check.SCALE_IN_MAX_MULTIPLE) / tick.close
        )
        assert decision.shares <= expected_max_shares

    def test_scale_in_max_multiple_override_produces_smaller_add(self):
        """2026-07-24 follow-up: gentler cap than the module default should
        cap the add smaller, holding everything else equal."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        default_trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True)
        gentle_trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True,
                                                   scale_in_max_multiple=1.5)
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=11.1)  # +11%, saturates both caps

        default_decision = default_trader(tick, portfolio)
        gentle_decision = gentle_trader(tick, portfolio)
        assert default_decision.decision == gentle_decision.decision == "BUY"
        assert gentle_decision.shares < default_decision.shares

    def test_scale_in_max_per_day_blocks_second_same_day_scale_in(self):
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True, scale_in_max_per_day=1)
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.5)

        first = trader(tick, portfolio)
        second = trader(tick, portfolio)  # same tick.timestamp -> same calendar day
        assert first.decision == "BUY"
        assert second.decision == "HOLD"


class TestMaxPositionsCap:
    def _new_ticker_tick_portfolio(self, held_tickers, cash=100_000.0):
        positions = {t: Position(ticker=t, shares=1, entry_price=10.0, entry_time=TS, current_price=10.0)
                     for t in held_tickers}
        portfolio = Portfolio(cash=cash, positions=positions)
        tick = Tick(timestamp=TS, ticker="NEW", open=20.0, high=20.0, low=20.0, close=20.0,
                     volume=100_000, rsi=55.0)
        return tick, portfolio

    def test_uncapped_by_default_allows_new_ticker(self):
        frames = make_frames("NEW", rsi=55.0, macd_hist=0.1)
        trader = replay_check.make_trader(frames, "v1.0")  # max_positions defaults None
        tick, portfolio = self._new_ticker_tick_portfolio(held_tickers=[f"T{i}" for i in range(30)])
        decision = trader(tick, portfolio)
        assert decision.decision == "BUY"

    def test_capped_blocks_new_ticker_at_cap(self):
        frames = make_frames("NEW", rsi=55.0, macd_hist=0.1)
        trader = replay_check.make_trader(frames, "v1.1", max_positions=25)
        tick, portfolio = self._new_ticker_tick_portfolio(held_tickers=[f"T{i}" for i in range(25)])
        decision = trader(tick, portfolio)
        assert decision.decision == "HOLD"
        assert "cap" in decision.rationale

    def test_capped_allows_room_below_cap(self):
        frames = make_frames("NEW", rsi=55.0, macd_hist=0.1)
        trader = replay_check.make_trader(frames, "v1.1", max_positions=25)
        tick, portfolio = self._new_ticker_tick_portfolio(held_tickers=[f"T{i}" for i in range(24)])
        decision = trader(tick, portfolio)
        assert decision.decision == "BUY"


class TestStrategyBuildersRegistered:
    def test_v17_and_capped25_present_alongside_existing_variants(self):
        assert set(replay_check.STRATEGY_BUILDERS.keys()) == {
            "v1.0", "v1.1", "v1.2", "v1.1-capped25", "v1.7", "v1.7-daily", "v1.7-gentle", "v1.0-trail"}
        assert set(replay_check.VARIANT_LABELS.keys()) == set(replay_check.STRATEGY_BUILDERS.keys())


class TestTrailingStop:
    """2026-07-28: win-rate investigation — trailing-stop breaches are the
    dominant real-world loss category (11 of 14 losses), but the harness
    never modeled a trailing stop at all before this. trailing_stop_pct
    defaults to None (not simulated) so every pre-existing variant is
    unaffected unless it explicitly opts in."""

    def _held_tick_portfolio(self, close_price, entry_price=10.0, entry_time=TS):
        pos = Position(ticker="XYZ", shares=10, entry_price=entry_price, entry_time=entry_time,
                        current_price=close_price)
        portfolio = Portfolio(cash=5000.0, positions={"XYZ": pos})
        tick = Tick(timestamp=TS, ticker="XYZ", open=close_price, high=close_price,
                     low=close_price, close=close_price, volume=100_000, rsi=55.0)
        return tick, portfolio

    def test_disabled_by_default(self):
        """A drop from peak that would breach a 5% trail stays a HOLD when
        trailing_stop_pct isn't passed — proves existing variants (none of
        which pass it) are unaffected."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.0")  # trailing_stop_pct defaults None
        tick1, portfolio = self._held_tick_portfolio(close_price=10.8)  # peak
        trader(tick1, portfolio)
        tick2, _ = self._held_tick_portfolio(close_price=10.2)  # -5.6% from peak, still +2% from entry
        decision = trader(tick2, portfolio)
        assert decision.decision == "HOLD"

    def test_breaches_on_drop_from_peak_even_when_pnl_still_positive(self):
        """The whole point of a trailing stop: it fires on drop-from-peak,
        not drop-from-entry — so it can trigger even while pnl_pct is still
        positive (locking in a gain), something the flat stop_loss_pct
        check alone could never do."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.0", trailing_stop_pct=5.0)
        tick1, portfolio = self._held_tick_portfolio(close_price=10.8)  # peak, +8% pnl
        first = trader(tick1, portfolio)
        assert first.decision == "HOLD"
        tick2, _ = self._held_tick_portfolio(close_price=10.2)  # peak*0.95 = 10.26, 10.2 < 10.26
        second = trader(tick2, portfolio)
        assert second.decision == "SELL"
        assert "trailing_stop_pct breached" in second.rationale

    def test_no_sell_while_price_stays_above_trail_line(self):
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.0", trailing_stop_pct=5.0)
        tick1, portfolio = self._held_tick_portfolio(close_price=10.8)
        trader(tick1, portfolio)
        tick2, _ = self._held_tick_portfolio(close_price=10.3)  # above 10.26 trail line
        decision = trader(tick2, portfolio)
        assert decision.decision == "HOLD"

    def test_peak_only_ratchets_up(self):
        """A pullback that doesn't breach the trail must not lower the
        tracked peak — a later new high still raises it, and the trail
        stays anchored to the true peak, not the most recent price."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.make_trader(frames, "v1.0", trailing_stop_pct=5.0)
        tick1, portfolio = self._held_tick_portfolio(close_price=10.5)
        tick2, _ = self._held_tick_portfolio(close_price=10.3)  # pullback, above 10.5*0.95=9.975
        tick3, _ = self._held_tick_portfolio(close_price=11.0)  # new peak
        tick4, _ = self._held_tick_portfolio(close_price=10.5)  # above 11.0*0.95=10.45, but WOULD
                                                                  # breach if trail were still anchored to 10.3
        assert trader(tick1, portfolio).decision == "HOLD"
        assert trader(tick2, portfolio).decision == "HOLD"
        assert trader(tick3, portfolio).decision == "HOLD"
        assert trader(tick4, portfolio).decision == "HOLD"

    def test_v1_0_trail_variant_registered_and_simulates_trailing_stop(self):
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        trader = replay_check.STRATEGY_BUILDERS["v1.0-trail"](frames)
        tick1, portfolio = self._held_tick_portfolio(close_price=10.8)
        trader(tick1, portfolio)
        tick2, _ = self._held_tick_portfolio(close_price=10.2)
        decision = trader(tick2, portfolio)
        assert decision.decision == "SELL"
        assert "trailing_stop_pct breached" in decision.rationale
