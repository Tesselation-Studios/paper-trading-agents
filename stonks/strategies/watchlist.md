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
- FUBO — open position

## Candidates
- AMC — idle_ticks: 0 — legacy meme carryover, re-evaluate under small-cap/diversification mandate ($2.45 fits universe)
- DJT — idle_ticks: 0 — legacy meme carryover, re-evaluate under small-cap/diversification mandate ($9.72 fits universe)

_Dropped 2026-07-20 nightly: HOOD (realized -12% loss, above universe cap), COIN (confirmed above $50 max-price cap)._
_Last touched: 2026-07-20 (nightly maintenance — pruned stale, need 3+ fresh sub-$15 names by Tue EOD)_
