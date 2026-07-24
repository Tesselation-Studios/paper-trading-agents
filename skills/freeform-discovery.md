# Skill: Freeform Discovery

Runs 08:00 ET daily via the `stonks-freeform-discovery` cron (pre-market, no trading — don't call `scripts/executor.py`). Companion to `scripts/discovery_scan.py`'s deterministic RSI/volume screen (still runs on its own schedule, unchanged) — this is the *judgment* half: real news, real catalysts, not mechanical matching.

## What to do

1. Read `strategies/watchlist.md` + `positions/*.md` first — don't waste a search on names already tracked.
2. Use `web_search`, `tavily_search` (try `topic: "finance"` or `"news"`), `tavily_extract`, `browser`, or `scripts/news_collector.py` freely — earnings surprises, contract wins, FDA news, unusual chatter, sector rotation. Genuine judgment, not a checklist.
3. For each real candidate: `python3 scripts/append_discovery.py --ticker TICKER --price PRICE --note "why this is real" [--source freeform]`. Rejects automatically if the price is outside today's bankroll-scaled universe band — no need to check that yourself first.
4. Aim for a small number of genuinely interesting names, not a wall of noise. This supplements the mechanical screen, it doesn't need to outproduce it — quality over quantity.
5. Not a trading tick — no `active.md` update. A 3-5 line note in `off_hours/YYYY-MM-DD.md` is enough (what you found, what you skipped and why).

## Format compatibility

`append_discovery.py` writes into the same `discoveries/YYYY-MM-DD.md` file `discovery_scan.py` uses (appends, dedupes by ticker) — `scripts/merge_discoveries.py`'s every-tick merge picks both up identically, no separate handling needed downstream.
