# Watchlist — Growing/Shrinking Candidate List

This is the discovery mechanism for the MVP: no ML, no news-source aggregation yet. Names get added when something's noticed (sentiment blip, unusual volume, a mention worth tracking) and dropped after `params.json: watchlist.idle_ticks_before_drop` ticks with nothing happening. Reset `idle_ticks` to 0 whenever a name is touched (mentioned in a decision, even a HOLD-with-reason).

Format: `TICKER — idle_ticks: N — note`

## Currently Held (always on the list, idle_ticks doesn't apply while open)
- F — open position (2 shares @ $14.135)
- FHB — open position (1 share @ $28.60, entry 2026-07-24 09:55)
- BFST — open position (1 share @ $30.98, entry 2026-07-24 10:05)
- IP — open position (2 shares @ $38.43, entry 2026-07-24 10:05 — filled 2 due to parallel tick collision, intended 1)
- BOX — open position (1 share @ $28.84, entry 2026-07-24 11:50 — v1.5 probe, Technology)

## Closed Positions
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
- MFAO — idle_ticks: 10 — from 2026-07-24.md ($25.38, RSI 50.9, MACDh +0.0531, vol 0.59x — too thin)

- DBX — idle_ticks: 11 — $28.43, RSI 48.8, MACDh +0.7735 🟢, vol 0.95x, PE 14.5x, Technology (cloud storage). Analyst Hold, PT ~$26.50. Earnings 8/6.
- AMC — idle_ticks: 11 — $2.28, RSI 56.7, MACDh +0.0306 🟢, vol 0.83x. Entertainment. Meme volatility risk.

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
_Last touched: 2026-07-24 13:31 ET (bumped idle_ticks: MFAO→10, DBX→11, AMC→11)._
