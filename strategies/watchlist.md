# Watchlist — Growing/Shrinking Candidate List

This is the discovery mechanism for the MVP: no ML, no news-source aggregation yet. Names get added when something's noticed (sentiment blip, unusual volume, a mention worth tracking) and dropped after `params.json: watchlist.idle_ticks_before_drop` ticks with nothing happening. Reset `idle_ticks` to 0 whenever a name is touched (mentioned in a decision, even a HOLD-with-reason).

Format: `TICKER — idle_ticks: N — note`

## Currently Held (always on the list, idle_ticks doesn't apply while open)
- F — open position (2 shares @ $14.135)

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
- FDIV — idle_ticks: 14 — $27.55, RSI 50.6, vol 4.56x (!), news: Emerson/Aramco corrosion monitoring collab (+0.95)
- COAG — idle_ticks: 14 — $49.73 mid, RSI 61.5, vol 0.95x, news: Hemab clinical data (neutral)
- MATE — idle_ticks: 14 — $29.95 mid, RSI 60.7, vol 0.85x, no news — technical only
- SRET — idle_ticks: 2 — $22.72, RSI 49.8, vol 1.07x, news: mortgage rates at lowest since Feb 2023 (sentiment -0.97)

_(RKT idle_ticks=24 and CLF idle_ticks=24 dropped 2026-07-23 11:10 — hit threshold.)_


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
_Last touched: 2026-07-23 09:40 ET (all 8 HOLD, no breaches or MACDh flips, RKT/CLF idle→16, CLF Q2 earnings +17.5% but gated CHOPPY)._  
