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
- ~~FUBO~~ — closed 2026-07-21 (MACDh bearish flip, -5.04%)
- ~~AMC~~ — closed 2026-07-21 (trailing stop breach, -1.69%)

## Candidates
- DJT — idle_ticks: 9 — legacy meme carryover, re-evaluate under small-cap/diversification mandate ($9.79 fits universe)
- TEVA — idle_ticks: 9 — probe discovery 07/20: generic pharma, opioid settlement behind, specialty pipeline growing ($31.36)
- AEO — idle_ticks: 9 — probe discovery 07/20: retail near lows, back-to-school catalyst, high SI squeeze potential ($17.43)
- RDDT — idle_ticks: 9 — probe discovery 07/20: post-IPO social, AI data licensing, limited comp ($184.52 — monitor, near cap)
- UBER — idle_ticks: 9 — probe discovery 07/20: duopoly, FCF-positive, AV optionality ($72.38)
- AFRM — idle_ticks: 9 — probe discovery 07/20: BNPL leader, rate-cut beneficiary, Amazon/Shopify/Walmart network ($76.99)
- UPST — idle_ticks: 9 — probe discovery 07/20: AI lending, beaten down, rate-cut re-fi catalyst ($29.09 — fits small-cap well)
- OXY — idle_ticks: 9 — probe discovery 07/20: Berkshire stake, carbon capture, energy sector gap ($55.50)
- TOST — idle_ticks: 9 — probe discovery 07/20: restaurant tech, growing merchant base ($24 area)

_Dropped 2026-07-20 nightly: HOOD (realized -12% loss, above universe cap), COIN (confirmed above $50 max-price cap)._
_Dropped 2026-07-21: AMC (closed position — trailing stop breach, -1.69%)._
_Last touched: 2026-07-21 13:50 ET (idle_ticks bumped to 7)._
