# Watchlist — Growing/Shrinking Candidate List

This is the discovery mechanism for the MVP: no ML, no news-source aggregation yet. Names get added when something's noticed (sentiment blip, unusual volume, a mention worth tracking) and dropped after `params.json: watchlist.idle_ticks_before_drop` ticks with nothing happening. Reset `idle_ticks` to 0 whenever a name is touched (mentioned in a decision, even a HOLD-with-reason).

Format: `TICKER — idle_ticks: N — note`

## Currently Held (always on the list, idle_ticks doesn't apply while open)
- BFST — open position (1 share @ $30.98, entry 2026-07-24 10:05)
- BOX — open position (3 shares @ $29.75, entry 2026-07-24 11:50 + scale-in 2026-07-27 11:40 — Technology)
- KRC — open position (2 shares @ $39.51, entry 2026-07-27 10:38 — Real Estate)
- BCS — open position (1 share @ $28.28, entry 2026-07-27 11:35 — Financial)

## Closed Positions
- ~~FHB~~ — closed 2026-07-27 09:59 (MACDh flip, v1.9 mandatory exit, -2.41%)
- ~~IP~~ — closed 2026-07-27 09:58 (unintentional parallel-process sale, +10.62%)
- ~~F~~ — closed 2026-07-27 09:37 (pre-earnings exit before Q2 7/28, +4.07%)
- ~~WSC~~ — closed 2026-07-23 (MACDh flip, v1.4 mandatory exit, +1.46%)
- ~~NVDA~~ — closed 2026-07-23 (MACDh flip, v1.4 mandatory exit, +0.45%)
- ~~CHWY~~ — closed 2026-07-23 (MACDh flip, v1.4 mandatory exit, -0.02%)
- ~~KHC~~ — closed 2026-07-23 (MACDh flip, v1.4 mandatory exit, -1.16%)
- ~~DVN~~ — closed 2026-07-23 (MACDh flip, v1.4 mandatory exit, +1.89%)
- ~~SNAP~~ — closed 2026-07-23 (MACDh flip, v1.4 mandatory exit, -4.59%)
- ~~GME~~ — closed 2026-07-23 (trailing stop breach, -5.00%)
- ~~OPEN~~ — closed 2026-07-23 (trailing stop breach, -5.11%)
- ~~SOFI~~ — closed 2026-07-22 (broke $17 support, Game Plan cut, -4.58%)
- ~~LYFT~~ — closed 2026-07-22 (trailing stop breach, -5.49%)
- ~~DJT~~ — closed 2026-07-22 (trailing stop breach, -5.2%)
- ~~MVST~~ — closed 2026-07-22 (trailing stop breach, +0.29%)
- ~~FUBO~~ — closed 2026-07-21 (MACDh bearish flip, -5.04%)
- ~~AMC~~ — closed 2026-07-21 (trailing stop breach, -1.69%)
- ~~MARA~~ — closed 2026-07-21 (trailing stop breach, +2.11%)

## Candidates
- ~~KRC~~ — entered 2026-07-27 10:38, 1 share @ $39.49, probe entry
- ~~BCS~~ — entered 2026-07-27 11:35, 1 share @ $28.28, probe entry
- NTRS — idle_ticks: 16 — $180.52, RSI 53.6 🟢, vol 1.19x. MS UW PT $173 BELOW current price — disqualified. Financial.
- GL — idle_ticks: 16 — $173.48, RSI 45.1 🟢, vol 1.18x. KBW Outperform PT $190. DISQUALIFIED: bankroll ceiling $54.25. Insurance.
- DXC — idle_ticks: 16 — $10.05, RSI 59.2 🟢, MACDh +0.0016 near-zero, vol 0.77x <1.0x. Technology.
- RMD — idle_ticks: 16 — $195.27, RSI 45.7 🟢, MACD bearish (-0.2514), vol 0.66x. Healthcare.
- AVEX — idle_ticks: 16 — $14.29, RSI 39.3 below band, MACD bearish, vol 0.36x. Defense/aerospace.
- PL — idle_ticks: 16 — $20.47, RSI 27.9 oversold, MACD bearish, vol 1.21x, sentiment -0.445. Earth obs satellites.
- TOST — idle_ticks: 16 — $29.04, RSI 53.5 🟢, MACD 🟢 +0.9865, vol 0.38x too thin. Truist PT $33. Sentiment +0.768. Restaurant tech.
- BKSY — idle_ticks: 16 — $21.57, RSI 35.8 below band, MACD bearish, vol 0.86x, -5.31% today. Defense imaging.
- RCAT — idle_ticks: 16 — $7.64, RSI 37.4 below band, MACD bearish, vol 0.69x, -4.86% today, sentiment +0.212. Defense/drones.

- ~~VTEX~~ — dropped 2026-07-27 09:33 (idle_ticks=24 threshold, vol 0.45x never crossed 1.0x entry bar)

~~MFAO~~ — dropped 2026-07-24 14:45 (idle_ticks=24 threshold, vol 0.59x, never crossed 1.0x entry bar)
~~DBX~~ — dropped 2026-07-24 14:40 (idle_ticks=24 threshold, vol 0.95x never crossed 1.0x entry bar)
~~AMC~~ — dropped 2026-07-24 14:40 (idle_ticks=24 threshold, vol 0.83x meme risk, never qualified)
~~OZKAP~~ — dropped 2026-07-24 11:40 (idle_ticks=24 threshold, also do-not-retry — preferred stock, never filled)
~~BFST~~ — entered 2026-07-24 10:05, 1 share @ $30.98, probe entry
~~NKLR~~ — dropped 2026-07-24 11:40 (idle_ticks=24 threshold, bearish MACD, vol <1.0x)
~~XRPNU~~ — dropped 2026-07-24 10:05 (dead ticker, vol 0.0, MACD bearish)
~~BEDY~~ — dropped 2026-07-24 11:15 (idle_ticks=24 threshold)
~~OLP~~ — dropped 2026-07-24 11:15 (idle_ticks=24 threshold)
- ~~IP~~ — entered 2026-07-24 10:05, 2 shares @ $38.43, probe entry (parallel tick collision)
- ~~FHB~~ — entered 2026-07-24 09:55, 1 share @ $28.60, probe entry
- ~~SRET~~ — dropped 2026-07-24 09:20 (idle_ticks=24 threshold)

_(IDT $62.53 skipped 2026-07-24 02:33 — above $50 universe cap.)_
_(FDIV idle_ticks=24, COAG idle_ticks=24, MATE idle_ticks=24 dropped 2026-07-23 15:20 — hit threshold.)_
_(RKT idle_ticks=24 and CLF idle_ticks=24 dropped 2026-07-23 11:10 — hit threshold.)_
_(OZKAP idle_ticks=24 dropped 2026-07-24 11:40 — do-not-retry, preferred stock never filled.)_
_(NKLR idle_ticks=24 dropped 2026-07-24 11:40 — bearish MACD, vol <1.0x.)_


_(All 5 candidates dropped 2026-07-22 14:00 — hit idle_ticks=24 threshold: IOVA, FVRR, GT, COTY, KSS.)_

_(Updated 2026-07-22 14:45 — RSI data populated for all 4 candidates via web search; idle_ticks reset to 0; RKT flagged below 45 band.)_
_(Updated 2026-07-22 14:40 — idle_ticks bumped to 2 for all 4 candidates.)_
_(Updated 2026-07-22 14:20 — MVST closed, trailing stop breach.)_

_(All 8 candidates dropped 2026-07-21 15:40 — hit idle_ticks=24 threshold. Watchlist empty — need fresh small-cap discovery next session.)_

_Dropped 2026-07-20 nightly: HOOD (realized -12% loss, above universe cap), COIN (confirmed above $50 max-price cap)._
_Dropped 2026-07-21: AMC (closed position -- trailing stop breach, -1.69%)._
_Dropped 2026-07-22 11:20: BROS ($65), BWA ($65), ROKU ($143), PLTR ($127) — above $50 universe cap._
_Dropped 2026-07-22 11:55: JOBY, ACHR, RIG, MYGN — hit idle_ticks=24 threshold._
_Last touched: 2026-07-27 11:55 ET (all HOLD. 9 candidates idle→14. All 9 disqualified. Pipeline starved 5 sessions.)_
