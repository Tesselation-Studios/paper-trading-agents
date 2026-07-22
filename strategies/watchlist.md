# Watchlist — Growing/Shrinking Candidate List

This is the discovery mechanism for the MVP: no ML, no news-source aggregation yet. Names get added when something's noticed (sentiment blip, unusual volume, a mention worth tracking) and dropped after `params.json: watchlist.idle_ticks_before_drop` ticks with nothing happening. Reset `idle_ticks` to 0 whenever a name is touched (mentioned in a decision, even a HOLD-with-reason).

Format: `TICKER — idle_ticks: N — note`

## Currently Held (always on the list, idle_ticks doesn't apply while open)
- NVDA — open position
- CHWY — open position
- KHC — open position
- SOFI — open position
- MVST — open position
- F — open position
- LYFT — open position
- GME — open position
- DJT — open position
- OPEN — open position
- SNAP — open position
- ~~FUBO~~ — closed 2026-07-21 (MACDh bearish flip, -5.04%)
- ~~AMC~~ — closed 2026-07-21 (trailing stop breach, -1.69%)
- ~~MARA~~ — closed 2026-07-21 (trailing stop breach, +2.11%)

## Candidates
- PLTR — idle_ticks: 6 — from 2026-07-22.md
- IOVA — idle_ticks: 6 — from 2026-07-22.md
- BROS — idle_ticks: 6 — from 2026-07-22.md
- JOBY — idle_ticks: 6 — from 2026-07-22.md
- ROKU — idle_ticks: 6 — from 2026-07-22.md
- BWA — idle_ticks: 6 — from 2026-07-22.md

- ACHR — idle_ticks: 6 — from 2026-07-21.md
- RIG — idle_ticks: 6 — from 2026-07-21.md
- FVRR — idle_ticks: 6 — from 2026-07-21.md
- MYGN — idle_ticks: 6 — from 2026-07-21.md
- GT — idle_ticks: 6 — from 2026-07-21.md
- COTY — idle_ticks: 6 — from 2026-07-21.md
- KSS — idle_ticks: 6 — from 2026-07-21.md

_(All 8 candidates dropped 2026-07-21 15:40 — hit idle_ticks=24 threshold. Watchlist empty — need fresh small-cap discovery next session.)_

_Dropped 2026-07-20 nightly: HOOD (realized -12% loss, above universe cap), COIN (confirmed above $50 max-price cap)._
_Dropped 2026-07-21: AMC (closed position -- trailing stop breach, -1.69%)._
_Dropped 2026-07-21 15:40: All 8 candidates (TEVA, AEO, RDDT, UBER, AFRM, UPST, OXY, TOST) — idle_ticks=24 threshold met._
_Last touched: 2026-07-22 09:40 ET (idle_ticks → 6, all 13 candidates)._
