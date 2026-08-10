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
import replay_check  # noqa: E402
import trader_db  # noqa: E402
from replay_harness import Tick, Portfolio, Position  # noqa: E402

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
        doesn't fire first; big_winner's +6% pnl is enough to hit the
        SCALE_IN_MAX_MULTIPLE cap (1.5x as of 2026-08-04, at 1.5x
        SCALE_IN_MIN_PNL_PCT = 4.5%) while small_winner's +3.5% isn't,
        which is what keeps the size difference visible rather than both
        saturating the same cap."""
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
        # +7% pnl — still under the 10% profit-target exit (v1.20), but far
        # beyond what SCALE_IN_MAX_MULTIPLE (saturates at 4.5%) should let
        # the size multiple grow past.
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.7)
        decision = trader(tick, portfolio)
        assert decision.decision == "BUY"
        expected_max_shares = int(
            (portfolio.total_equity * replay_check.MAX_POSITION_PCT * replay_check.ENTRY_CONVICTION
             * replay_check.SCALE_IN_SIZE_FACTOR * replay_check.SCALE_IN_MAX_MULTIPLE) / tick.close
        )
        assert decision.shares <= expected_max_shares

    def test_scale_in_max_multiple_override_produces_smaller_add(self):
        """2026-07-24 follow-up: a gentler cap should cap the add smaller,
        holding everything else equal. Compares two explicit overrides
        (not "default vs override") since 2026-08-04 promoted the module
        default itself to 1.5 -- see SCALE_IN_MAX_MULTIPLE's comment --
        so this now tests the override mechanism directly rather than
        depending on the module default being the looser of the two."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5)
        loose_trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True,
                                                  scale_in_max_multiple=3.0)
        gentle_trader = replay_check.make_trader(frames, "v1.1", scale_into_winners=True,
                                                   scale_in_max_multiple=1.5)
        tick, portfolio = self._held_tick_portfolio(entry_price=10.0, close_price=10.7)  # +7%, saturates both caps, still under the 10% profit-target exit

        loose_decision = loose_trader(tick, portfolio)
        gentle_decision = gentle_trader(tick, portfolio)
        assert loose_decision.decision == gentle_decision.decision == "BUY"
        assert gentle_decision.shares < loose_decision.shares

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
            "v1.0", "v1.1", "v1.2", "v1.1-capped25", "v1.7", "v1.7-daily", "v1.7-gentle",
            "v1.0-trail", "v1.0-trail-vol"}
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
        tick3, _ = self._held_tick_portfolio(close_price=10.9)  # new peak, +9% -- stays under the 10% profit-target exit (v1.20)
        tick4, _ = self._held_tick_portfolio(close_price=10.5)  # above 10.9*0.95=10.355, but WOULD
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
        # 2026-08-01: trail widened 5%->7% (params.json) -- 10.8*0.93=10.044,
        # so the breach price moved from 10.2 to below 10.044.
        tick2, _ = self._held_tick_portfolio(close_price=9.9)
        decision = trader(tick2, portfolio)
        assert decision.decision == "SELL"
        assert "trailing_stop_pct breached" in decision.rationale


class TestVolScaledTrailingStop:
    """2026-07-28: NOT calendar-time-based like the already-rejected
    stop_patience.py — trail_pct = TRAILING_STOP_PCT * (1 + TRAIL_K *
    vol_20d), clamped to [TRAIL_MIN_PCT, TRAIL_MAX_PCT]."""

    def _held_tick_portfolio(self, close_price, entry_price=10.0, entry_time=TS):
        pos = Position(ticker="XYZ", shares=10, entry_price=entry_price, entry_time=entry_time,
                        current_price=close_price)
        portfolio = Portfolio(cash=5000.0, positions={"XYZ": pos})
        tick = Tick(timestamp=TS, ticker="XYZ", open=close_price, high=close_price,
                     low=close_price, close=close_price, volume=100_000, rsi=55.0)
        return tick, portfolio

    def test_higher_volatility_widens_the_trail(self):
        """Same peak-then-drop path: a quiet ticker (vol_20d~0) breaches at
        the ~5% base trail; a volatile one (vol_20d=0.05) gets a wide enough
        trail (11.25% at the default TRAIL_K=25) to absorb the same dip."""
        quiet_frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5, vol_20d=0.0)
        quiet_trader = replay_check.make_trader(quiet_frames, "v1.0", vol_scaled_trail=True)
        tick1, quiet_portfolio = self._held_tick_portfolio(close_price=10.8)
        quiet_trader(tick1, quiet_portfolio)
        # 2026-08-01: base trail widened 5%->7% -- 10.8*0.93=10.044, so the
        # breach price moved from 10.2 to below 10.044.
        tick2, _ = self._held_tick_portfolio(close_price=9.9)
        quiet_decision = quiet_trader(tick2, quiet_portfolio)
        assert quiet_decision.decision == "SELL"

        volatile_frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5, vol_20d=0.05)
        volatile_trader = replay_check.make_trader(volatile_frames, "v1.0", vol_scaled_trail=True)
        tick1b, volatile_portfolio = self._held_tick_portfolio(close_price=10.8)
        volatile_trader(tick1b, volatile_portfolio)
        tick2b, _ = self._held_tick_portfolio(close_price=10.2)
        volatile_decision = volatile_trader(tick2b, volatile_portfolio)
        assert volatile_decision.decision == "HOLD"

    def test_clamped_at_trail_max_pct(self):
        """An absurdly high vol_20d must not push the trail past TRAIL_MAX_PCT
        (18% as of 2026-08-01, was 12%) -- the exact unbounded-widening
        failure mode stop_patience.py already demonstrated is a bad idea.
        Explicitly pins stop_loss_pct/profit_target_pct to -10%/12% (rather
        than the live params.json defaults, currently -6%/10% as of v1.20)
        because at an 18% trail max, no entry/peak combo can simultaneously
        keep peak pnl under a 10% profit target AND keep the trail-clamp
        boundary above a -6% hard-stop floor -- 0.82*peak > 0.94*entry
        requires peak/entry > 1.146, but pnl<10% requires peak/entry < 1.10.
        Isolating this test's stop/target from live drift is the fix, not
        chasing new boundary numbers each time params.json changes."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5, vol_20d=1.0)
        trader = replay_check.make_trader(frames, "v1.0", vol_scaled_trail=True,
                                           stop_loss_pct=-10.0, profit_target_pct=12.0)
        tick1, portfolio = self._held_tick_portfolio(close_price=100.0, entry_price=90.0)
        trader(tick1, portfolio)
        # 18% trail from peak 100 -> stop at 82.0
        tick_above, _ = self._held_tick_portfolio(close_price=82.5, entry_price=90.0)
        assert trader(tick_above, portfolio).decision == "HOLD"
        tick_below, _ = self._held_tick_portfolio(close_price=81.5, entry_price=90.0)
        assert trader(tick_below, portfolio).decision == "SELL"

    def test_clamped_at_trail_min_pct(self):
        """A negative trail_k override must not push the trail below
        TRAIL_MIN_PCT (7% as of 2026-08-01, was 4%), proving the floor side
        of the clamp works too."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5, vol_20d=0.5)
        trader = replay_check.make_trader(frames, "v1.0", vol_scaled_trail=True, trail_k=-10.0)
        tick1, portfolio = self._held_tick_portfolio(close_price=100.0, entry_price=95.0)
        trader(tick1, portfolio)
        # floor is 7% trail from peak 100 -> stop at 93.0, not lower
        tick_above, _ = self._held_tick_portfolio(close_price=93.5, entry_price=95.0)
        assert trader(tick_above, portfolio).decision == "HOLD"
        tick_below, _ = self._held_tick_portfolio(close_price=92.5, entry_price=95.0)
        assert trader(tick_below, portfolio).decision == "SELL"

    def test_missing_vol_20d_falls_back_to_base_trail(self):
        """NaN (real insufficient-rolling-history shape, not a literal
        None -- ma20/ma50 elsewhere in this module use pd.notna() for the
        same reason) must not propagate into the formula."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5, vol_20d=float("nan"))
        trader = replay_check.make_trader(frames, "v1.0", vol_scaled_trail=True)
        tick1, portfolio = self._held_tick_portfolio(close_price=10.8)
        trader(tick1, portfolio)
        # v=0 -> effective_trail = TRAILING_STOP_PCT = 7% (was 5%) -> stop at 10.044
        tick_above, _ = self._held_tick_portfolio(close_price=10.3)
        assert trader(tick_above, portfolio).decision == "HOLD"
        tick_below, _ = self._held_tick_portfolio(close_price=9.9)
        assert trader(tick_below, portfolio).decision == "SELL"

    def test_vol_scaled_trail_overrides_flat_trailing_stop_pct(self):
        """Passing both is a caller error in intent, but vol_scaled_trail
        must win — proves the precedence, not just that both work alone."""
        frames = make_frames("XYZ", rsi=55.0, macd_hist=0.5, vol_20d=0.05)
        trader = replay_check.make_trader(frames, "v1.0", trailing_stop_pct=5.0, vol_scaled_trail=True)
        tick1, portfolio = self._held_tick_portfolio(close_price=10.8)
        trader(tick1, portfolio)
        # flat 5% would breach at 10.2 (stop 10.26); vol-scaled (11.25% @ vol=0.05) should not
        tick2, _ = self._held_tick_portfolio(close_price=10.2)
        assert trader(tick2, portfolio).decision == "HOLD"
