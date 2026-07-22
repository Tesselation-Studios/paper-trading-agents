# Watchlist — Growing/Shrinking Candidate List

This is the discovery mechanism for the MVP: no ML, no news-source aggregation yet. Names get added when something's noticed (sentiment blip, unusual volume, a mention worth tracking) and dropped after `params.json: watchlist.idle_ticks_before_drop` ticks with nothing happening. Reset `idle_ticks` to 0 whenever a name is touched (mentioned in a decision, even a HOLD-with-reason).

Format: `TICKER — idle_ticks: N — note`

## Currently Held (always on the list, idle_ticks doesn't apply while open)
- NVDA — open position
- CHWY — open position
- KHC — open position
- ~~SOFI~~ — closed 2026-07-22 (broke $17 support, Game Plan cut, -4.58%)
- MVST — open position
- F — open position
- ~~LYFT~~ — closed 2026-07-22 (trailing stop breach, -5.49%)
- GME — open position
- ~~DJT~~ — closed 2026-07-22 (trailing stop breach, -5.2%)
- ~~MVST~~ — closed 2026-07-22 (trailing stop breach, +0.29%)
- OPEN — open position
- SNAP — open position
- WSC — open position
- DVN — open position
- ~~FUBO~~ — closed 2026-07-21 (MACDh bearish flip, -5.04%)
- ~~AMC~~ — closed 2026-07-21 (trailing stop breach, -1.69%)
- ~~MARA~~ — closed 2026-07-21 (trailing stop breach, +2.11%)

## Candidates
- RKT — idle_ticks: 8 — RSI(14)=38.6 (below band), from 2026-07-22.md
- WSC — idle_ticks: 0 — RSI(14)=63.1 (in band), held, earnings Jul 30-Aug 3, from 2026-07-22.md
- CLF — idle_ticks: 8 — RSI(14)=45.6 (barely in band, skipped 14:55/15:00/15:05/15:10/15:15/15:20/15:25/15:35/15:40), from 2026-07-22.md


_(All 5 candidates dropped 2026-07-22 14:00 — hit idle_ticks=24 threshold: IOVA, FVRR, GT, COTY, KSS.)_

_(Updated 2026-07-22 14:45 — RSI data populated for all 4 candidates via web search; idle_ticks reset to 0; RKT flagged below 45 band.)_
_(Updated 2026-07-22 14:40 — idle_ticks bumped to 2 for all 4 candidates.)_
_(Updated 2026-07-22 14:20 — MVST closed, trailing stop breach.)_

_(All 8 candidates dropped 2026-07-21 15:40 — hit idle_ticks=24 threshold. Watchlist empty — need fresh small-cap discovery next session.)_

_Dropped 2026-07-20 nightly: HOOD (realized -12% loss, above universe cap), COIN (confirmed above $50 max-price cap)._
_Dropped 2026-07-21: AMC (closed position -- trailing stop breach, -1.69%)._
_Dropped 2026-07-21 15:40: All 8 candidates (TEVA, AEO, RDDT, UBER, AFRM, UPST, OXY, TOST) — idle_ticks=24 threshold met._
_Dropped 2026-07-22 11:20: BROS ($65), BWA ($65), ROKU ($143), PLTR ($127) — above $50 universe cap._
_Dropped 2026-07-22 11:55: JOBY, ACHR, RIG, MYGN — hit idle_ticks=24 threshold._
_Dropped 2026-07-22 11:50: none. All candidates below idle threshold (24)._
_Last touched: 2026-07-22 15:40 ET (all HOLD, CLF/RKT idle→8, WSC held)._
