"""Counterfactual analysis — what if Stan had bought something else?

Answers: "Stan bought DVN on July 22. Were there better alternatives
in the same price/liquidity range that day?"

Two main classes:
  - UniverseSampler: finds peer stocks in the same price/volume band
  - CounterfactualReplay: replays Stan's entry logic against alternatives

Usage:
    from src.counterfactual import UniverseSampler, CounterfactualReplay

    sampler = UniverseSampler()
    alternatives = sampler.sample_alternatives("DVN", "2026-07-22", n=50)

    replay = CounterfactualReplay()
    result = replay.run(
        symbol="DVN",
        date="2026-07-22",
        entry_price=150.0,
        alternatives=alternatives,
    )
    result.print_report()
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from bar_loader import BarLoader, DEFAULT_BARS_DIR
from replay_harness import Tick, Portfolio, TraderDecision, ReplayHarness, ReplayResult

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_PRICE_RANGE_PCT = 0.30  # ±30% of target price
DEFAULT_MIN_VOLUME = 500        # minimum average daily volume
DEFAULT_N_ALTERNATIVES = 50     # how many alternatives to sample
DEFAULT_MIN_PRICE = 5.0         # skip penny stocks
DEFAULT_MAX_PRICE = 500.0       # skip extremely expensive stocks


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class AlternativeResult:
    """Result of replaying entry logic against one alternative."""

    symbol: str
    triggered: bool          # would Stan's entry logic have fired?
    entry_price: float       # price at entry signal
    entry_time: datetime     # when the signal fired
    next_day_return_pct: float  # % return from entry to next close
    next_day_close: float    # next day's close price
    conviction: float        # conviction score at entry
    rationale: str           # why it triggered (or didn't)


@dataclass
class CounterfactualResult:
    """Complete counterfactual analysis for one actual trade."""

    actual_symbol: str
    actual_date: str
    actual_entry_price: float
    actual_return_pct: float       # Stan's actual return
    n_alternatives_tested: int
    n_triggered: int               # how many alternatives would have triggered
    alternatives: List[AlternativeResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def triggered_results(self) -> List[AlternativeResult]:
        """Only alternatives that WOULD have triggered an entry."""
        return [a for a in self.alternatives if a.triggered]

    @property
    def missed_opportunities(self) -> List[AlternativeResult]:
        """Alternatives that triggered AND beat Stan's actual return."""
        return [
            a for a in self.triggered_results
            if a.next_day_return_pct > self.actual_return_pct
        ]

    def summary(self) -> str:
        """One-line summary."""
        n_better = len(self.missed_opportunities)
        total_triggered = len(self.triggered_results)
        return (
            f"{self.actual_symbol} ({self.actual_return_pct:+.1f}%) — "
            f"{total_triggered}/{self.n_alternatives_tested} alternatives triggered, "
            f"{n_better} would have beaten it"
        )

    def print_report(self, top_n: int = 10) -> None:
        """Print a ranked report of missed opportunities."""
        print(f"\n{'='*70}")
        print(f"Counterfactual: {self.actual_symbol} on {self.actual_date}")
        print(f"  Stan's entry: ${self.actual_entry_price:.2f} → {self.actual_return_pct:+.2f}%")
        print(f"  Alternatives tested: {self.n_alternatives_tested}")
        print(f"  Triggered: {len(self.triggered_results)}")
        print(f"  Would beat: {len(self.missed_opportunities)}")
        print(f"{'='*70}")

        if not self.triggered_results:
            print("  No alternatives would have triggered.")
            return

        # Sort by return descending
        ranked = sorted(self.alternatives, key=lambda a: a.next_day_return_pct, reverse=True)

        print(f"\n{'Symbol':<10} {'Trigger':>8} {'Entry':>10} {'Return':>10} {'Conv':>7}  Rationale")
        print("-" * 70)
        for a in ranked[:top_n]:
            marker = "★" if a.next_day_return_pct > self.actual_return_pct else " "
            print(
                f"{marker}{a.symbol:<9} {'YES' if a.triggered else 'NO':>8} "
                f"${a.entry_price:>9.2f} {a.next_day_return_pct:>+9.2f}% "
                f"{a.conviction:>6.1f}%  {a.rationale[:40]}"
            )

        if self.errors:
            print(f"\n  Errors ({len(self.errors)}):")
            for e in self.errors[:5]:
                print(f"    - {e}")

    @property
    def best_alternative(self) -> Optional[AlternativeResult]:
        """The single best alternative (highest return)."""
        triggered = self.triggered_results
        if not triggered:
            return None
        return max(triggered, key=lambda a: a.next_day_return_pct)


# ── UniverseSampler ──────────────────────────────────────────────────────────


class UniverseSampler:
    """Sample peer stocks from the bar cache.

    Filters the universe to stocks in a configurable price range
    with sufficient liquidity, then returns random samples.

    Args:
        bars_dir: Directory containing <ticker>.parquet files.
        bar_loader: Pre-configured BarLoader (optional, created from bars_dir).
        min_price: Minimum price to consider (skip penny stocks).
        max_price: Maximum price to consider.
        min_volume: Minimum average daily volume for liquidity.
        seed: Random seed for reproducibility.
    """

    def __init__(
        self,
        bars_dir: Optional[Path] = None,
        bar_loader: Optional[BarLoader] = None,
        min_price: float = DEFAULT_MIN_PRICE,
        max_price: float = DEFAULT_MAX_PRICE,
        min_volume: int = DEFAULT_MIN_VOLUME,
        seed: int = 42,
    ):
        self.bars_dir = Path(bars_dir) if bars_dir else DEFAULT_BARS_DIR
        self._loader = bar_loader or BarLoader(bars_dir=self.bars_dir)
        self.min_price = min_price
        self.max_price = max_price
        self.min_volume = min_volume
        self._rng = random.Random(seed)
        self._universe_cache: Optional[Dict[str, Dict[str, Any]]] = None

    @property
    def available_symbols(self) -> List[str]:
        """All tickers with parquet files in the bars directory."""
        return sorted(
            p.stem for p in self.bars_dir.glob("*.parquet")
            if p.stem not in ("SPY", "^GSPC", "^VIX")  # skip indices
        )

    def price_range_for_date(
        self,
        symbol: str,
        date_str: str,
    ) -> Optional[Tuple[float, float]]:
        """Get the high/low price range for a symbol on a given date.

        Args:
            symbol: Ticker symbol.
            date_str: ISO date string (e.g. "2026-07-22").

        Returns:
            (min_price, max_price) tuple or None if no data.
        """
        ticks = self._loader.load_date_range([symbol], date_str, date_str, interval_minutes=30)
        if not ticks:
            return None
        closes = [t.close for t in ticks]
        return (min(closes), max(closes))

    def get_avg_close(
        self,
        symbol: str,
        date_str: str,
    ) -> Optional[float]:
        """Average close price for a symbol on a given date."""
        ticks = self._loader.load_date_range([symbol], date_str, date_str, interval_minutes=30)
        if not ticks:
            return None
        closes = [t.close for t in ticks]
        return float(np.mean(closes))

    def build_universe(
        self,
        date_str: str,
        min_volume: Optional[int] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Build a universe of tradeable stocks for a given date.

        Scans all available parquet files, computes average price and
        volume for the date, and filters to the configured ranges.

        Args:
            date_str: ISO date string.
            min_volume: Override minimum volume threshold.

        Returns:
            Dict mapping symbol → {"avg_price": float, "avg_volume": float}.
        """
        vol_threshold = min_volume if min_volume is not None else self.min_volume
        universe: Dict[str, Dict[str, Any]] = {}

        for sym in self.available_symbols:
            ticks = self._loader.load_date_range(
                [sym], date_str, date_str, interval_minutes=30,
            )
            if not ticks:
                continue

            closes = [t.close for t in ticks]
            volumes = [t.volume for t in ticks]
            avg_price = float(np.mean(closes))
            avg_volume = float(np.mean(volumes))

            if avg_price < self.min_price or avg_price > self.max_price:
                continue
            if avg_volume < vol_threshold:
                continue

            universe[sym] = {"avg_price": avg_price, "avg_volume": avg_volume}

        log.info(
            "Universe for %s: %d symbols (price $%.0f-$%.0f, vol≥%d)",
            date_str, len(universe), self.min_price, self.max_price, vol_threshold,
        )
        return universe

    def sample_alternatives(
        self,
        symbol: str,
        date_str: str,
        n: int = DEFAULT_N_ALTERNATIVES,
        price_range_pct: float = DEFAULT_PRICE_RANGE_PCT,
        exclude_symbol: bool = True,
    ) -> List[str]:
        """Sample N alternative symbols in the same price/volume range.

        Args:
            symbol: The reference symbol (Stan's actual purchase).
            date_str: ISO date string.
            n: Number of alternatives to sample.
            price_range_pct: ± price range as fraction (0.30 = ±30%).
            exclude_symbol: Exclude the reference symbol from results.

        Returns:
            List of ticker symbols (up to n, fewer if universe is small).
        """
        # Get the reference price
        ref_price = self.get_avg_close(symbol, date_str)
        if ref_price is None:
            log.warning("No price data for %s on %s — using full universe", symbol, date_str)
            ref_price = 50.0  # default fallback

        price_min = ref_price * (1 - price_range_pct)
        price_max = ref_price * (1 + price_range_pct)

        # Build or retrieve universe
        if self._universe_cache is None:
            self._universe_cache = self.build_universe(date_str)

        # Filter to price-adjacent symbols
        candidates = [
            s for s, info in self._universe_cache.items()
            if price_min <= info["avg_price"] <= price_max
        ]
        if exclude_symbol and symbol in candidates:
            candidates.remove(symbol)

        log.info(
            "%s @ $%.2f → %d candidates in [%.2f, %.2f] (±%.0f%%)",
            symbol, ref_price, len(candidates), price_min, price_max, price_range_pct * 100,
        )

        # Sample
        if len(candidates) <= n:
            result = candidates
        else:
            result = self._rng.sample(candidates, n)

        return result


# ── CounterfactualReplay ─────────────────────────────────────────────────────


class CounterfactualReplay:
    """Replay Stan's entry logic against alternative stocks.

    For a given day's actual trade, loads bars for each alternative,
    runs the entry gate logic (same as make_decision_with_strategy /
    ConfigVariant entry gates), and records what would have triggered.

    Entry gate (mirrors replay_check + overnight_replay):
      - RSI between entry_min and entry_max (default: 50-70)
      - Volume > min_mult × 20d avg volume (default: 2.0x)
      - Price above MA (default: 20-period)
      - MACD bullish (fast > slow)
      - Conviction ≥ min_conviction (default: 0.60)

    Args:
        bars_dir: Directory containing <ticker>.parquet files.
        bar_loader: Pre-configured BarLoader.
        rsi_entry_min: Minimum RSI for entry (default 50).
        rsi_entry_max: Maximum RSI for entry (default 70).
        volume_min_mult: Minimum volume multiplier vs 20d avg (default 2.0).
        conviction_min: Minimum conviction to trigger entry (default 0.60).
        rsi_period: RSI calculation period (default 14).
        ma_period: Moving average period (default 20).
        macd_fast: MACD fast EMA period (default 12).
        macd_slow: MACD slow EMA period (default 26).
        seed: Random seed (currently unused, reserved).
    """

    def __init__(
        self,
        bars_dir: Optional[Path] = None,
        bar_loader: Optional[BarLoader] = None,
        rsi_entry_min: float = 50.0,
        rsi_entry_max: float = 70.0,
        volume_min_mult: float = 2.0,
        conviction_min: float = 0.60,
        rsi_period: int = 14,
        ma_period: int = 20,
        macd_fast: int = 12,
        macd_slow: int = 26,
        seed: int = 42,
    ):
        self.bars_dir = Path(bars_dir) if bars_dir else DEFAULT_BARS_DIR
        self._loader = bar_loader or BarLoader(bars_dir=self.bars_dir)

        # Entry gate parameters
        self.rsi_entry_min = rsi_entry_min
        self.rsi_entry_max = rsi_entry_max
        self.volume_min_mult = volume_min_mult
        self.conviction_min = conviction_min
        self.rsi_period = rsi_period
        self.ma_period = ma_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow

    def run(
        self,
        symbol: str,
        date_str: str,
        entry_price: float,
        alternatives: List[str],
        lookback_days: int = 30,
    ) -> CounterfactualResult:
        """Run counterfactual analysis for one actual trade.

        Args:
            symbol: The symbol Stan actually bought.
            date_str: ISO date string of the trade.
            entry_price: Stan's actual entry price.
            alternatives: List of alternative symbols to test.
            lookback_days: How many days of bars to load before date_str
                (needed for indicator calculation).

        Returns:
            CounterfactualResult with per-alternative results.
        """
        result = CounterfactualResult(
            actual_symbol=symbol,
            actual_date=date_str,
            actual_entry_price=entry_price,
            actual_return_pct=0.0,
            n_alternatives_tested=len(alternatives),
            n_triggered=0,
        )

        # ── Get Stan's actual next-day return ──────────────────────
        actual_return = self._compute_next_day_return(symbol, date_str)
        result.actual_return_pct = actual_return

        # ── Compute next trading day for return calculations ──────
        next_date = self._next_trading_day(date_str)

        # ── Run each alternative ──────────────────────────────────
        date_dt = datetime.fromisoformat(date_str)
        start_dt = date_dt - timedelta(days=lookback_days)
        start_str = start_dt.strftime("%Y-%m-%d")

        for alt in alternatives:
            try:
                alt_result = self._evaluate_alternative(
                    alt, date_str, start_str, next_date,
                )
                result.alternatives.append(alt_result)
                if alt_result.triggered:
                    result.n_triggered += 1
            except Exception as e:
                log.debug("Alternative %s failed: %s", alt, e)
                result.errors.append(f"{alt}: {e}")
                result.alternatives.append(AlternativeResult(
                    symbol=alt,
                    triggered=False,
                    entry_price=0.0,
                    entry_time=date_dt,
                    next_day_return_pct=0.0,
                    next_day_close=0.0,
                    conviction=0.0,
                    rationale=f"Error: {e}",
                ))

        return result

    def _evaluate_alternative(
        self,
        symbol: str,
        date_str: str,
        start_str: str,
        next_date: str,
    ) -> AlternativeResult:
        """Evaluate whether Stan's entry logic would have triggered for one alternative.

        Args:
            symbol: Alternative ticker.
            date_str: The trade date.
            start_str: Lookback start date (for indicator calculation).
            next_date: Next trading day (for return calculation).

        Returns:
            AlternativeResult with trigger status and return.
        """
        # Load bars for the date (with lookback for indicators)
        end_str = date_str
        ticks = self._loader.load_date_range(
            [symbol], start_str, end_str, interval_minutes=30,
        )

        if not ticks:
            return AlternativeResult(
                symbol=symbol,
                triggered=False,
                entry_price=0.0,
                entry_time=datetime.fromisoformat(date_str),
                next_day_return_pct=0.0,
                next_day_close=0.0,
                conviction=0.0,
                rationale="No bar data",
            )

        # ── Compute indicators from the full tick series ──────────
        closes = np.array([t.close for t in ticks], dtype=float)
        volumes = np.array([t.volume for t in ticks], dtype=float)

        rsi_arr = self._compute_rsi(closes, self.rsi_period)
        ma_arr = self._compute_sma(closes, self.ma_period)
        macd_f_arr = self._compute_sma(closes, self.macd_fast)
        macd_s_arr = self._compute_sma(closes, self.macd_slow)
        vol_ma_arr = self._compute_sma(volumes, 20)

        # ── Filter to just the target date ────────────────────────
        target_date_only = self._filter_to_date(ticks, date_str)
        if not target_date_only:
            return AlternativeResult(
                symbol=symbol,
                triggered=False,
                entry_price=0.0,
                entry_time=datetime.fromisoformat(date_str),
                next_day_return_pct=0.0,
                next_day_close=0.0,
                conviction=0.0,
                rationale="No bars on target date",
            )

        # ── Run entry gate on each tick of the target date ────────
        triggered = False
        best_entry: Optional[Tick] = None
        best_conviction = 0.0
        best_rationale = "No signal"

        for tick in target_date_only:
            idx = next(
                (j for j, t in enumerate(ticks)
                 if t.timestamp == tick.timestamp and t.ticker == tick.ticker),
                -1,
            )
            if idx < 0:
                continue

            rsi = rsi_arr[idx]
            ma = ma_arr[idx]
            macd_f = macd_f_arr[idx]
            macd_s = macd_s_arr[idx]
            vol_ma = vol_ma_arr[idx]

            # Skip if any indicator is missing
            if any(v is None for v in [rsi, ma, macd_f, macd_s, vol_ma]):
                continue

            volume_ratio = tick.volume / vol_ma if vol_ma > 0 else 0.0
            price_above = tick.close > ma
            macd_bullish = macd_f > macd_s

            # ── Entry gates (same as overnight_replay ConfigVariant) ──
            rsi_ok = self.rsi_entry_min <= float(rsi) <= self.rsi_entry_max
            vol_ok = volume_ratio >= self.volume_min_mult
            ma_ok = price_above
            macd_ok = macd_bullish

            # Conviction scoring
            conviction = 0.0
            if rsi_ok:
                conviction += 0.30
            if vol_ok:
                conviction += 0.25
            if ma_ok:
                conviction += 0.15
            if macd_ok:
                conviction += 0.15
            conviction = min(conviction + 0.15, 1.0)  # +0.15 for volume as catalyst

            if conviction >= self.conviction_min and rsi_ok and vol_ok:
                triggered = True
                if conviction > best_conviction:
                    best_conviction = conviction
                    best_entry = tick
                    best_rationale = (
                        f"RSI={rsi:.1f} VolR={volume_ratio:.2f} "
                        f"MA={'▲' if price_above else '▼'} MACD={'▲' if macd_bullish else '▼'}"
                    )

        if not triggered or best_entry is None:
            return AlternativeResult(
                symbol=symbol,
                triggered=False,
                entry_price=float(closes[-1]),
                entry_time=target_date_only[-1].timestamp if target_date_only else datetime.fromisoformat(date_str),
                next_day_return_pct=0.0,
                next_day_close=0.0,
                conviction=float(best_conviction),
                rationale=best_rationale if best_conviction > 0 else "No entry signal",
            )

        # ── Compute next-day return ────────────────────────────────
        next_return = self._compute_next_day_return(symbol, date_str)

        return AlternativeResult(
            symbol=symbol,
            triggered=True,
            entry_price=best_entry.close,
            entry_time=best_entry.timestamp,
            next_day_return_pct=next_return,
            next_day_close=0.0,  # computed separately if needed
            conviction=best_conviction,
            rationale=best_rationale,
        )

    def _compute_next_day_return(
        self,
        symbol: str,
        date_str: str,
    ) -> float:
        """Compute the % return from date_str's close to next trading day's close.

        Args:
            symbol: Ticker symbol.
            date_str: ISO date string.

        Returns:
            Percentage return (e.g., 2.1 means +2.1%).
        """
        next_date = self._next_trading_day(date_str)

        # Load today's data
        today_ticks = self._loader.load_date_range(
            [symbol], date_str, date_str, interval_minutes=30,
        )
        if not today_ticks:
            return 0.0

        # Load next day's data
        next_ticks = self._loader.load_date_range(
            [symbol], next_date, next_date, interval_minutes=30,
        )
        if not next_ticks:
            return 0.0

        today_close = today_ticks[-1].close
        next_close = next_ticks[-1].close

        if today_close <= 0:
            return 0.0

        return ((next_close - today_close) / today_close) * 100.0

    @staticmethod
    def _next_trading_day(date_str: str) -> str:
        """Find the next trading day (skip weekends).

        Simple heuristic: if Friday → Monday, else next day.
        Does NOT account for holidays — sufficient for counterfactual analysis.

        Args:
            date_str: ISO date string.

        Returns:
            ISO date string for next trading day.
        """
        dt = datetime.fromisoformat(date_str)
        next_dt = dt + timedelta(days=1)
        if next_dt.weekday() == 5:  # Saturday → Monday
            next_dt += timedelta(days=2)
        elif next_dt.weekday() == 6:  # Sunday → Monday
            next_dt += timedelta(days=1)
        return next_dt.strftime("%Y-%m-%d")

    @staticmethod
    def _filter_to_date(ticks: List[Tick], date_str: str) -> List[Tick]:
        """Filter ticks to only those on a specific date."""
        target_date = datetime.fromisoformat(date_str).date()
        return [t for t in ticks if t.timestamp.date() == target_date]

    # ── Indicator helpers ──────────────────────────────────────────

    @staticmethod
    def _compute_rsi(closes: np.ndarray, period: int = 14) -> List[Optional[float]]:
        """Wilder's RSI."""
        result: List[Optional[float]] = [None] * len(closes)
        if len(closes) < period + 1:
            return result
        for i in range(period, len(closes)):
            deltas = np.diff(closes[i - period:i + 1])
            gains = np.maximum(deltas, 0)
            losses = np.abs(np.minimum(deltas, 0))
            avg_gain = np.mean(gains)
            avg_loss = np.mean(losses)
            if avg_loss < 1e-10:
                result[i] = 100.0 if avg_gain > 0 else 50.0
            else:
                rs = avg_gain / avg_loss
                result[i] = 100.0 - (100.0 / (1.0 + rs))
        return result

    @staticmethod
    def _compute_sma(values: np.ndarray, period: int) -> List[Optional[float]]:
        """Simple moving average."""
        result: List[Optional[float]] = [None] * len(values)
        if len(values) < period:
            return result
        running_sum = float(np.sum(values[:period]))
        result[period - 1] = running_sum / period
        for i in range(period, len(values)):
            running_sum += values[i] - values[i - period]
            result[i] = running_sum / period
        return result


# ── Multi-trade runner ───────────────────────────────────────────────────────


def run_counterfactual_batch(
    trades: List[Dict[str, Any]],
    bars_dir: Optional[Path] = None,
    n_alternatives: int = DEFAULT_N_ALTERNATIVES,
    price_range_pct: float = DEFAULT_PRICE_RANGE_PCT,
) -> List[CounterfactualResult]:
    """Run counterfactual analysis across multiple trades.

    Args:
        trades: List of trade dicts, each with at least:
            - symbol (str)
            - date (str, ISO format) or entry_time (str/datetime)
            - entry_price (float)
        bars_dir: Path to parquet bar files.
        n_alternatives: Number of alternatives to test per trade.
        price_range_pct: ± price range as fraction.

    Returns:
        List of CounterfactualResult, one per trade.
    """
    sampler = UniverseSampler(bars_dir=bars_dir)
    replay = CounterfactualReplay(bars_dir=bars_dir)

    results: List[CounterfactualResult] = []

    for trade in trades:
        symbol = trade.get("symbol", trade.get("ticker", ""))
        if not symbol:
            log.warning("Trade missing symbol — skipping: %s", trade)
            continue

        # Extract date from either 'date' or 'entry_time' field
        date_str = trade.get("date", "")
        if not date_str:
            entry_time = trade.get("entry_time", "")
            if entry_time:
                if isinstance(entry_time, datetime):
                    date_str = entry_time.strftime("%Y-%m-%d")
                else:
                    # Parse string like "2026-07-22T10:30:00" or "2026-07-22"
                    date_str = str(entry_time)[:10]
        if not date_str:
            log.warning("Trade missing date — skipping: %s", trade)
            continue

        entry_price = float(trade.get("entry_price", trade.get("price", 0)))
        if entry_price <= 0:
            log.warning("Trade %s missing entry_price — skipping", symbol)
            continue

        try:
            alternatives = sampler.sample_alternatives(
                symbol, date_str, n=n_alternatives,
                price_range_pct=price_range_pct,
            )
            if not alternatives:
                log.warning("No alternatives found for %s on %s", symbol, date_str)
                continue

            result = replay.run(symbol, date_str, entry_price, alternatives)
            results.append(result)
        except Exception as e:
            log.error("Counterfactual failed for %s on %s: %s", symbol, date_str, e)

    return results


def print_batch_report(
    results: List[CounterfactualResult],
    top_n: int = 5,
) -> None:
    """Print a summary report across all trades.

    Args:
        results: List of counterfactual results.
        top_n: Number of missed opportunities to show per trade.
    """
    if not results:
        print("No counterfactual results to report.")
        return

    print(f"\n{'='*70}")
    print(" COUNTERFACTUAL BATCH REPORT")
    print(f"{'='*70}")
    print(f"  Trades analyzed: {len(results)}")

    total_triggered = sum(r.n_triggered for r in results)
    total_alternatives = sum(r.n_alternatives_tested for r in results)
    total_missed = sum(len(r.missed_opportunities) for r in results)

    print(f"  Total alternatives tested: {total_alternatives}")
    print(f"  Total triggered: {total_triggered}")
    print(f"  Missed opportunities (would beat Stan): {total_missed}")

    # Collect all triggered alternatives across all trades
    all_triggered: List[Tuple[str, AlternativeResult, CounterfactualResult]] = []
    for r in results:
        for a in r.triggered_results:
            all_triggered.append((r.actual_symbol, a, r))

    # Rank by return
    all_triggered.sort(key=lambda x: x[1].next_day_return_pct, reverse=True)

    print(f"\n{'='*70}")
    print(" TOP MISSED OPPORTUNITIES (across all trades)")
    print(f"{'='*70}")
    print(f"{'Alt':<8} {'Stan':<6} {'Alt Ret':>8} {'Stan Ret':>8} {'Delta':>8} {'Date':<12} {'Conv':>6}")
    print("-" * 70)

    shown = 0
    for actual_sym, alt, parent_result in all_triggered:
        if alt.next_day_return_pct > parent_result.actual_return_pct:
            delta = alt.next_day_return_pct - parent_result.actual_return_pct
            print(
                f"{alt.symbol:<8} {actual_sym:<6} "
                f"{alt.next_day_return_pct:>+7.1f}% {parent_result.actual_return_pct:>+7.1f}% "
                f"{delta:>+7.1f}% {parent_result.actual_date:<12} {alt.conviction:>5.1f}%"
            )
            shown += 1
            if shown >= top_n:
                break

    if shown == 0:
        print("  (no alternatives beat Stan's returns)")
