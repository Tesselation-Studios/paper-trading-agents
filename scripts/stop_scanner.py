#!/usr/bin/env python3
"""Stop-loss / trailing-stop scanning — run once per tick against open
positions. Extracted 2026-08-11 from executor.py. Depends on alpaca_client.py
(Alpaca REST calls), guardrail_gates.py (_size_cap_pct_for, for the
oversized-position trim's conviction-play cap), and protective_stops.py
(INDEX_ANCHOR_TICKERS, the index-anchor upside exemption) -- extracted last
since it needed all three.
"""
import json
import math
from datetime import datetime, timezone
from typing import Any, Dict, List

import alpaca_client
import guardrail_gates
import protective_stops
import trader_db

def _load_stop_state() -> Dict[str, Any]:
    if alpaca_client.STOPS_STATE_PATH.exists():
        try:
            return json.loads(alpaca_client.STOPS_STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_stop_state(state: Dict[str, Any]) -> None:
    alpaca_client.STATE_DIR.mkdir(exist_ok=True)
    alpaca_client.STOPS_STATE_PATH.write_text(json.dumps(state, indent=2))


def _fetch_vol_20d(account: str, tickers: List[str]) -> Dict[str, float]:
    """Fetch 20-day daily-bar volatility (std of daily returns) for each
    ticker from Alpaca. Returns {ticker: vol_20d} dict — tickers with
    insufficient history (< 20 bars or all-zero returns) get vol_20d=0.0
    (falls back to the flat trailing_stop_pct base).

    Uses the same urllib.request pattern as other Alpaca calls in this file.
    Called exclusively by check_stops() when trailing_stop_mode='volatility_scaled'.
    Best-effort: never raises, returns empty dict on API error.
    """
    import urllib.request
    import urllib.parse
    from datetime import datetime, timezone, timedelta

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=60)  # 60 calendar days to guarantee ~40 trading days
    params = urllib.parse.urlencode({
        "symbols": ",".join(tickers),
        "timeframe": "1Day",
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "feed": "iex",
        "limit": 30,
    })
    url = f"{alpaca_client.ALPACA_BASE_URL}/v2/stocks/bars?{params}"
    req = urllib.request.Request(url, headers=alpaca_client.get_headers(account))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return {}

    result: Dict[str, float] = {}
    for ticker in tickers:
        bars = data.get("bars", {}).get(ticker, [])
        if len(bars) < 20:
            result[ticker] = 0.0
            continue
        closes = [b.get("c", 0.0) for b in bars]
        returns = [(closes[i] - closes[i-1]) / closes[i-1]
                    for i in range(1, len(closes)) if closes[i-1] > 0]
        if len(returns) < 20:
            result[ticker] = 0.0
            continue
        # Sample std (ddof=1), same as replay_check.py's vol_20d using rolling(20).std()
        import statistics
        vol = statistics.stdev(returns[-20:])
        result[ticker] = vol if vol > 0 else 0.0
    return result


def check_stops(account: str) -> List[Dict[str, Any]]:
    """Scan open positions for hard-stop, trailing-stop, or oversized-position
    breaches.

    Hard stop: risk.stop_loss_pct below entry price (fixed floor).
    Trailing stop: risk.trailing_stop_pct below the highest price observed
    since entry (ratchets up only; persisted to state/guardrail_stops.json).
    2026-07-27: briefly replaced with stop_patience.py (widening distances
    the longer a position is held) same day, then REVERTED same night —
    overnight split-window backtest (research/2026-07-27.md) found flat
    stops beat every patience variant tested (shipped params + 2 alternate
    ramp/bound speeds) on Sharpe and return, in both halves. Not a "wrong
    numbers" problem: rescued 6 trades, made 9 worse, net P&L down
    $268.58->$212.87 on the tested universe/window. stop_patience.py stays
    on disk (tests intact) for a future redesign, just unwired from here.
    Oversized: current market_value / portfolio_value exceeds
    risk.max_position_pct — this happens when a position grows past the cap
    via price appreciation (gate_position_size only blocks new BUYs from
    crossing the cap, it doesn't correct an existing position that's already
    over). Confirmed 2026-07-22: NVDA sat over cap 4 days / 3 nightly cycles
    with "trim it" as a journaled intention that never executed — mechanized
    here instead of relying on that being remembered again.
    Any of the three can be disabled via guardrail_gates.hard_stop /
    .trailing_stop / .position_size_trim.

    Returns breach dicts: {ticker, stop_type, reason, loss_pct, shares_to_sell
    (oversized only)}. Caller (tick loop) is responsible for actually
    executing the SELL.
    """
    params = alpaca_client.load_params()
    toggles = params.get("guardrail_gates", {})
    risk = params.get("risk", {})
    hard_stop_pct = abs(float(risk.get("stop_loss_pct", alpaca_client.DEFAULT_STOP_LOSS_PCT)))
    trailing_pct = float(risk.get("trailing_stop_pct", 5.0))
    max_position_pct = float(risk.get("max_position_pct", 6.0))
    trailing_stop_mode = risk.get("trailing_stop_mode", "flat")
    trail_k = float(risk.get("trail_k", 40.0))
    trail_min_pct = float(risk.get("trail_min_pct", 4.0))
    trail_max_pct = float(risk.get("trail_max_pct", 12.0))

    positions = alpaca_client.get_positions(account)
    portfolio_value = None
    if toggles.get("position_size_trim", True):
        account_data = alpaca_client.get_account(account)
        portfolio_value = float(account_data.get("equity", 0))

    state = _load_stop_state()
    breaches = []
    held_tickers = set()

    # Pre-fetch vol_20d for ALL held tickers once (not per-position loop)
    vol_20d_map: Dict[str, float] = {}
    if toggles.get("trailing_stop", True) and trailing_stop_mode == "volatility_scaled":
        held = [p["symbol"].upper() for p in positions]
        if held:
            vol_20d_map = _fetch_vol_20d(account, held)

    # Pre-fetch long-play metadata (play_type/predicted_by_date) for ALL
    # held tickers once -- Alpaca's alpaca_client.get_positions() above has no notion of
    # this, it's local trader_db.py state (2026-07-30, params.json
    # risk.long_play). Fail-open: a DB error here just means no ticker
    # gets long-play treatment this tick, falls through to normal stops.
    long_play_map: Dict[str, Dict[str, Any]] = {}
    conviction_play_tickers: set = set()
    if toggles.get("long_play", True) or toggles.get("conviction_play", True):
        try:
            conn = trader_db.get_conn()
            try:
                for row in trader_db.get_open_positions(conn):
                    if row.get("play_type") == "long" and row.get("predicted_by_date"):
                        long_play_map[str(row["ticker"]).upper()] = row
                    elif row.get("play_type") == "conviction":
                        conviction_play_tickers.add(str(row["ticker"]).upper())
            finally:
                conn.close()
        except Exception:
            long_play_map = {}
            conviction_play_tickers = set()
    today = datetime.now(timezone.utc).date()

    for p in positions:
        ticker = p["symbol"].upper()
        held_tickers.add(ticker)
        entry_price = float(p["avg_entry_price"])
        current_price = float(p["current_price"])
        market_value = float(p["market_value"])
        qty_held = int(float(p["qty"]))

        # Long-play resolution / trailing-stop exemption (2026-07-30,
        # params.json risk.long_play). A long play is exempt from the
        # normal trailing-stop schedule ONLY while its predicted_by_date
        # hasn't arrived yet -- the hard stop below still applies
        # unconditionally regardless, that circuit breaker is never
        # optional (see stop_patience.py's revert for why: letting
        # optimism override the true floor is exactly how blind patience
        # loses money). Once the date arrives, resolve mechanically right
        # here -- flips play_type back to 'standard' (so this fires once,
        # not every tick after) and labels the training_examples row so
        # hit rate is queryable later -- it does NOT force a sell; the
        # position just reverts to being judged on its own merits by the
        # normal stop schedule from this point on.
        is_active_long_play = False
        lp = long_play_map.get(ticker)
        if lp:
            try:
                deadline = datetime.strptime(lp["predicted_by_date"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                deadline = None
            if deadline and today < deadline:
                is_active_long_play = True
            elif deadline and today >= deadline:
                return_pct = (current_price - entry_price) / entry_price * 100 if entry_price else 0.0
                hit = return_pct > 0
                breaches.append({
                    "ticker": ticker, "stop_type": "long_play_resolved",
                    "reason": f"{ticker}: long play horizon reached (predicted_by_date {lp['predicted_by_date']}) -- "
                              f"prediction {'correct' if hit else 'incorrect'}, {return_pct:+.1f}% vs entry "
                              f"(\"{lp.get('prediction_reason', '')}\"). Reverting to standard trailing-stop schedule.",
                    "loss_pct": return_pct,
                    "long_play_hit": hit,
                })
                now_iso = datetime.now(timezone.utc).isoformat()
                try:
                    conn = trader_db.get_conn()
                    try:
                        trader_db.resolve_long_play(conn, ticker=ticker, updated_at=now_iso)
                        # Entry row only (2026-08-01) -- same fix as
                        # record_trade_close: "newest unlabeled row" would
                        # happily label a SELL/HOLD row that never carried
                        # the prediction being scored here.
                        te_row = trader_db.find_entry_training_example(
                            conn, ticker, position_entry_time=lp.get("entry_time"))
                        te_id = te_row["id"] if te_row else None
                        if te_id is not None:
                            trader_db.label_training_example(
                                conn, training_example_id=te_id, trade_id=None,
                                label_win=1 if hit else 0, label_return_pct=return_pct,
                                label_horizon="long_play_prediction",
                            )
                    finally:
                        conn.close()
                except Exception:
                    pass  # best-effort, matches the fail-open philosophy elsewhere in this function

        if portfolio_value and portfolio_value > 0 and current_price > 0:
            current_pct = market_value / portfolio_value * 100
            # 2026-08-10: conviction plays are sized to risk.conviction_play's
            # own (larger) cap by gate_position_size/_size_cap_pct_for at
            # entry -- this trim check was still comparing every position
            # against the flat max_position_pct regardless, so a conviction
            # play correctly sized above the flat 6% but under its own 10%
            # got force-trimmed minutes after entry as "oversized." Same bug
            # shape gate_position_size itself had until 2026-08-01 (see that
            # function's docstring), just never propagated to this sibling
            # check. Confirmed live: SPY index-anchor bought at 7.4%, trimmed
            # (closed, 1 share) 3.5 minutes later citing the 6% cap.
            #
            # 2026-08-10: index-anchor tickers (protective_stops.INDEX_ANCHOR_TICKERS) are a
            # further, deliberate exception on top of that -- exempt from
            # the oversized check entirely on the upside, not just capped at
            # conviction_play's (now 20%) cap. Current policy is "don't
            # actively buy more into it, sell it down for cash instead" (see
            # strategy.md), not "force-trim it back down" if it organically
            # grows past the standard conviction cap.
            is_index_anchor = ticker in conviction_play_tickers and ticker in protective_stops.INDEX_ANCHOR_TICKERS
            oversized_cap = (
                guardrail_gates._size_cap_pct_for(risk, "conviction")[0] if ticker in conviction_play_tickers
                else max_position_pct
            )
            if not is_index_anchor and current_pct > oversized_cap:
                target_value = portfolio_value * oversized_cap / 100
                excess_value = market_value - target_value
                shares_to_sell = min(qty_held, max(1, math.ceil(excess_value / current_price)))
                breaches.append({
                    "ticker": ticker, "stop_type": "oversized",
                    "reason": f"{ticker}: {current_pct:.1f}% of portfolio exceeds {oversized_cap:.0f}% cap "
                              f"(${market_value:,.2f} of ${portfolio_value:,.2f}) — trim {shares_to_sell} share(s)",
                    "loss_pct": (current_price - entry_price) / entry_price * 100 if entry_price else 0.0,
                    "shares_to_sell": shares_to_sell,
                })
                # Oversized is a trim, not an exit — still check hard/trailing
                # stops below, don't skip them the way a full-exit breach does.

        if toggles.get("hard_stop", True):
            hard_stop_price = entry_price * (1 - hard_stop_pct / 100)
            if current_price <= hard_stop_price:
                loss_pct = (current_price - entry_price) / entry_price * 100
                breaches.append({
                    "ticker": ticker, "stop_type": "hard",
                    "reason": f"{ticker}: {loss_pct:.1f}% loss >= {hard_stop_pct:.0f}% hard stop "
                              f"(${entry_price:.2f} -> ${current_price:.2f})",
                    "loss_pct": loss_pct,
                })
                continue  # already breached, don't also report trailing

        # Track peak regardless of whether trailing-stop gate is enabled, so
        # re-enabling it later doesn't start from a stale/reset peak.
        entry = state.get(ticker, {"peak_price": entry_price})
        peak_price = max(float(entry.get("peak_price", entry_price)), current_price)
        state[ticker] = {"peak_price": peak_price, "entry_price": entry_price}

        if toggles.get("trailing_stop", True) and not is_active_long_play:
            # 2026-07-30: vol-scaled trailing stop (trailing_stop_mode='volatility_scaled')
            # uses the same formula as replay_check.py's make_trader(vol_scaled_trail=True):
            #   trail_pct = trailing_stop_pct * (1 + trail_k * vol_20d), clamped to [trail_min_pct, trail_max_pct]
            # Research: 2026-07-28 trailing-stop.md — TRAIL_K=40 is best variant.
            # vol_20d_map is pre-fetched once above, not fetched per-position.
            effective_trail_pct = trailing_pct
            if trailing_stop_mode == "volatility_scaled":
                vol = vol_20d_map.get(ticker, 0.0)
                if vol > 0:
                    effective_trail_pct = max(trail_min_pct, min(trail_max_pct,
                                               trailing_pct * (1 + trail_k * vol)))
            if ticker in conviction_play_tickers:
                # Wider room for a short-term dip that doesn't break the
                # thesis -- not exempt from the trailing stop like a long
                # play, just less trigger-happy (params.json risk.
                # conviction_play.trail_multiplier).
                trail_multiplier = float(risk.get("conviction_play", {}).get("trail_multiplier", 1.5))
                effective_trail_pct = effective_trail_pct * trail_multiplier
            trail_stop_price = peak_price * (1 - effective_trail_pct / 100)
            if current_price <= trail_stop_price:
                drop_from_peak = (current_price - peak_price) / peak_price * 100
                breaches.append({
                    "ticker": ticker, "stop_type": "trailing",
                    "reason": f"{ticker}: trailing stop breached, {drop_from_peak:.1f}% off peak "
                              f"${peak_price:.2f} (stop ${trail_stop_price:.2f}, current ${current_price:.2f}, "
                              f"mode={trailing_stop_mode}, effective_trail={effective_trail_pct:.1f}%)",
                    "loss_pct": (current_price - entry_price) / entry_price * 100,
                })

    for ticker in list(state.keys()):
        if ticker not in held_tickers:
            del state[ticker]
    _save_stop_state(state)
    return breaches
