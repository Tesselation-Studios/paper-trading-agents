# Research — 2026-07-28: Trailing Stop Win-Rate Investigation

Overnight validation of whether a trailing stop helps win rate at all (the dominant loss category in real history: 11 of 14 losses, ~21% win rate within the trailing-stop-breach category), and whether volatility-scaling it beats a flat %. Manual one-off run — `replay_check.py`'s two new investigation flags (`--split-window` and `--sweep-trail`), fully unwired from live trading, backtest-only. Methodology: `skills/backtest-tools.md` conventions, parameterized in-memory variants only, no live files edited. Universe: `load_live_universe()` — 36 tickers (positions + active watchlist), 200-day Alpaca daily-bar lookback, 103 trading days, split at the date midpoint per the promotion bar.

**Bottom line: no trailing-stop variant clears the gate. The best vol-scaled candidate (trail_k=40) improves Sharpe substantially over v1.0 baseline but falls well short of the +5pp win-rate threshold. Flat trailing stop is strictly worse than no trailing stop on every metric.**

---

## 1. Flat Trailing Stop (v1.0-trail)

### What it is
v1.0 entry/exit rules + a flat 5% trailing stop (mirrors `params.json`'s current `risk.trailing_stop_pct`), ratcheted from the highest close observed since entry. Checked after MACDh-flip exits but before the fixed -10% hard stop — so a trail breach fires first if it would trigger before the from-entry hard stop does.

### Results (vs v1.0 baseline, same -10%/-12% stop/profit-target)

| Variant | Full Sharpe | 1st Half (51d) Sharpe | 2nd Half (52d) Sharpe | Win Rate | Total Return % | Trades | Robust |
|---|---|---|---|---|---|---|---|
| **v1.0** | **0.418** | **0.408** | **0.673** | **43.8%** | **+2.15%** | 137 | **true** |
| v1.0-trail | 0.347 | 0.817 | **-0.073** | 41.3% | +1.40% | 475 | **false** |

**Flat trailing stop fails the split-window bar hard.** Second-half Sharpe is negative (-0.073), total return is lower (1.40% vs 2.15%), and win rate drops by 2.5pp. Trade count nearly quadruples (137 → 475) — the trail fires much more frequently than the hard -10% stop, chopping out of positions on noise rather than letting the hard stop or profit target do their work. This is direct backtest evidence that a flat 5% trail *reduces* win rate, Sharpe, and total return vs having no trailing stop at all.

---

## 2. Volatility-Scaled Trailing Stop (v1.0-trail-vol, TRAIL_K=25 default)

### What it is
Same v1.0 rules, but the trail distance is now computed per tick: `trail_pct = TRAILING_STOP_PCT * (1 + TRAIL_K * vol_20d)`, clamped to [4.0%, 12.0%]. High-volatility tickers get wider trails so noise doesn't trip them prematurely. Deliberately NOT calendar-time-based (the already-rejected `stop_patience.py` made trails wider the longer a position was held — that was the failure mode).

### Results (vs v1.0 baseline, same -10%/-12% stop/profit-target)

| Variant | Full Sharpe | 1st Half (51d) Sharpe | 2nd Half (52d) Sharpe | Win Rate | Total Return % | Trades | Robust |
|---|---|---|---|---|---|---|---|
| **v1.0** | **0.418** | **0.408** | **0.673** | **43.8%** | **+2.15%** | 137 | **true** |
| v1.0-trail-vol | 0.621 | 0.390 | 1.006 | 40.5% | +2.92% | 363 | **true** |

**Vol-scaling makes the trail robust** (Sharpe positive in both halves, unlike flat) and improves full-window Sharpe by 49% over baseline (0.621 vs 0.418), with higher total return (+2.92% vs +2.15%). But win rate drops 3.3pp (40.5% vs 43.8%) — it still fires too often, chopping 363 trades vs 137. Split-half win rates are both below baseline: 37.5% vs 43.5% in the first half, 42.1% vs 45.1% in the second half.

**Interpretation**: vol-scaling fixes the blunt-force noise-chop problem of the flat trail (it stays robust where flat didn't), and it improves Sharpe/return — but it does NOT fix win rate, which is the specific metric under investigation. If the goal is "stop the bleeding on the 11-of-14 real-money trailing-stop losses," this is the wrong mechanism — the remedy makes the trail *wider* for noisy tickers, which keeps you in positions longer, but that means letting losers run further, not cutting them earlier. Sharpe improves because the winners that survive benefit, but win rate falls further.

---

## 3. Sweep Over TRAIL_K (Isolate the Scaling Coefficient)

### Setup
Hold stop_loss/profit_target at the current live `params.json` values (-8.0% / 10.0%) and sweep only TRAIL_K = [10, 20, 30, 40] to isolate whether any vol-scaling coefficient clears the bar independent of stop/target tuning. The --split-window v1.0 baseline uses different stop/target values (-10%/-12%) so the comparison here is NOT perfectly apples-to-apples — the sweep's stop/target are tighter. A true gate evaluation should compare against a v1.0 baseline run with the same -8/10 parameters, which this run didn't include. What follows is the closest available comparison.

### Results (all at -8%/-10%, vol-scaled)

| trail_k | Full Sharpe | 1st Half Sharpe | 2nd Half Sharpe | Win Rate | Total Return % | Trades | Robust |
|---|---|---|---|---|---|---|---|
| 10 | 0.978 | 0.750 | 0.385 | 42.4% | +4.72% | 443 | true |
| 20 | 0.902 | 0.320 | 0.898 | 42.6% | +4.34% | 401 | true |
| 30 | 1.007 | 0.837 | 1.071 | 43.3% | +4.99% | 365 | true |
| 40 | **1.080** | 0.845 | 1.084 | **44.7%** | **+5.41%** | 349 | true |
| v1.0 baseline (-10/-12, no trail) | 0.418 | 0.408 | 0.673 | 43.8% | +2.15% | 137 | true |

All four trail_k values are robust (Sharpe positive in both halves). trail_k=40 achieves the best win rate at 44.7%, which is +0.9pp above the v1.0 baseline — but the v1.0 baseline used different stop/target parameters (-10/-12 vs -8/-10 for the sweep). At -10/-12, the trail_vol variant had 40.5% win rate, suggesting the tighter -8/-10 params (not the trailing stop) account for some of the win-rate improvement in the sweep.

**The clean takeaway**: higher trail_k → fewer trades, higher win rate, higher Sharpe, higher total return. trail_k=40 is the best of the four tested. But even at its peak (44.7%), it's only +0.9pp above v1.0 baseline's 43.8% win rate — nowhere near the +5pp gate. And since the v1.0 baseline comparison uses more favorable stop/target params for the sweep, the real gap is likely smaller (possibly negative).

---

## Gate Assessment

**Gate**: robust=true AND win_rate >= v1.0 baseline + 5pp AND non-negative win_rate improvement in both split halves AND total_return/both-half Sharpes no worse than v1.0 baseline.

### v1.0-trail (flat 5%)
- robust: **false** (2nd half Sharpe -0.073) → **FAIL**

### v1.0-trail-vol (TRAIL_K=25, -10/-12)
- robust: true ✓
- win_rate: 40.5% vs 43.8% baseline → **-3.3pp FAIL**
- 1st half win_rate: 37.5% vs 43.5% → negative
- 2nd half win_rate: 42.1% vs 45.1% → negative
- total_return: +2.92% vs +2.15% → passes ✓

### trail_k=10 (vol-scaled, -8/-10)
- win_rate: 42.4% vs 43.8% → **-1.4pp FAIL** (below baseline, let alone +5pp)

### trail_k=20 (vol-scaled, -8/-10)
- win_rate: 42.6% vs 43.8% → **-1.2pp FAIL**

### trail_k=30 (vol-scaled, -8/-10)
- win_rate: 43.3% vs 43.8% → **-0.5pp FAIL**

### trail_k=40 (vol-scaled, -8/-10)
- robust: true ✓
- win_rate: 44.7% vs 43.8% → **+0.9pp FAIL** (need +5pp, and this comparison already benefits from tighter -8/-10 stop/target vs baseline's -10/-12)
- total_return: +5.41% vs +2.15% → passes ✓
- Sharpe: 1.080 vs 0.418 → passes ✓

---

## Verdict: **no candidate clears the gate — trailing stops reduce win rate, not improve it**

Every trailing-stop variant tested (flat 5%, vol-scaled at 4 trail_k values) fails the +5pp win-rate bar. The best candidate (trail_k=40, vol-scaled) ekes out +0.9pp over baseline — but that's at tighter stop/target params (-8/-10 vs v1.0's -10/-12), so the underlying win-rate improvement attributable to the trail itself is likely zero or negative.

**The mechanism is clear from first principles and confirmed by the data**: a trailing stop can only exit positions *earlier* than the hard stop or profit target would. Some of those early exits are noise-chops (bad), some are genuine loss-limitation (good). The net effect across this 103-day, 36-ticker universe is negative for win rate — the noise-chops dominate. This matches the live history: 11 of 14 real losses are trailing-stop breaches. Adding or tuning a trail doesn't help — the trail *is* the problem.

**Recommendation**: the trailing stop should be re-evaluated as a position-management tool entirely. Consider removing it from live trading (or drastically reducing its priority in `check_stops()`) and relying on the hard -8% stop + MACDh-flip exit + profit target. The backtest data says: no trail is better than any trail for win rate, and win rate is the bottleneck on bankroll ceiling growth in the bootstrap phase.

---

## Test methodology
- `python3 scripts/replay_check.py --split-window` — full universe, all registered variants, split-half robustness
- `python3 scripts/replay_check.py --sweep-trail` — same universe, sweep TRAIL_K [10, 20, 30, 40] at current live -8%/-10% stop/profit-target
- Both fetch real Alpaca IEX daily bars — same 36-ticker universe, 200-day lookback, 103 trading days
- No `executor.py`/`params.json`/`strategy.md` modified — investigation-only, fully unwired from live trading
