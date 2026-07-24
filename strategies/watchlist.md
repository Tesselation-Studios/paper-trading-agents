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
- OZKAP — idle_ticks: 2 — $16.38, RSI 57.2, vol 1.80x, news: Bank OZK dividend raise (sentiment +0.88)
- BFST — idle_ticks: 2 — $30.46, RSI 54.0, vol 1.68x, news: Q2 beat (sentiment +0.00)
- NKLR — idle_ticks: 2 — $5.42, RSI 57.2, vol 1.28x, news: NRC readiness review (sentiment +0.79)
- XRPNU — idle_ticks: 2 — $10.73, RSI 54.3, no news — technical signal only

- BEDY — idle_ticks: 6 — from 2026-07-23.md
- OLP — idle_ticks: 6 — from 2026-07-23.md
- IP — idle_ticks: 6 — from 2026-07-23.md
- FHB — idle_ticks: 6 — from 2026-07-23.md

- SRET — idle_ticks: 22 — $22.72, RSI 49.8, vol 1.07x, news: mortgage rates at lowest since Feb 2023 (sentiment -0.97)

_(IDT $62.53 skipped 2026-07-24 02:33 — above $50 universe cap.)_
_(FDIV idle_ticks=24, COAG idle_ticks=24, MATE idle_ticks=24 dropped 2026-07-23 15:20 — hit threshold.)_
_(RKT idle_ticks=24 and CLF idle_ticks=24 dropped 2026-07-23 11:10 — hit threshold.)_


_(All 5 candidates dropped 2026-07-22 14:00 — hit idle_ticks=24 threshold: IOVA, FVRR, GT, COTY, KSS.)_

_(Updated 2026-07-22 14:45 — RSI data populated for all 4 candidates via web search; idle_ticks reset to 0; RKT flagged below 45 band.)_
_(Updated 2026-07-22 14:40 — idle_ticks bumped to 2 for all 4 candidates.)_
_(Updated 2026-07-22 14:20 — MVST closed, trailing stop breach.)_

_(All 8 candidates dropped 2026-07-21 15:40 — hit idle_ticks=24 threshold. Watchlist empty — need fresh small-cap discovery next session.)_

_Dropped 2026-07-20 nightly: HOOD (realized -12% loss, above universe cap), COIN (confirmed above $50 max-price cap)._
_Dropped 2026-07-21: AMC (closed position -- trailing stop breach, -1.69%)._
_Dropped 2026-07-22 11:20: BROS ($65), BWA ($65), ROKU ($143), PLTR ($127) — above $50 universe cap._
_Dropped 2026-07-22 11:55: JOBY, ACHR, RIG, MYGN — hit idle_ticks=24 threshold._
_Last touched: 2026-07-24 09:05 ET (idle_ticks bumped across all 9 candidates)._
