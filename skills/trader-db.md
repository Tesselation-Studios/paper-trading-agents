# Skill: Trader DB Queries

`state/trader.db` (local SQLite) holds decisions, journal, training examples, news cache, and the Alpaca audit log. Never write raw SQL against it — use `scripts/trader_query.py`'s subcommands, JSON out.

```bash
python3 scripts/trader_query.py positions                    # all open positions
python3 scripts/trader_query.py positions --ticker AAA       # one ticker
python3 scripts/trader_query.py watchlist                    # current candidates
python3 scripts/trader_query.py bankroll                     # current ceiling/session stats
python3 scripts/trader_query.py bankroll --history            # recent win/loss log
python3 scripts/trader_query.py decisions --ticker AAA --limit 10
python3 scripts/trader_query.py training-examples --labeled-only
python3 scripts/trader_query.py news --ticker AAA --hours 24
python3 scripts/trader_query.py audit-log --limit 20
```

- `positions --ticker X` on an unknown ticker returns `{"error": ...}`, not a crash — check for `"error"` in the result before assuming a real position.
- `positions`/`watchlist` deliberately don't include current price/market value/unrealized P&L — Alpaca is the live source of truth for those (same "thesis storage, not a price mirror" principle as before). Pull live price/P&L from the executor status check, not this DB.
- Writes go through `scripts/trader_db.py`'s functions directly (`upsert_position`, `upsert_watchlist_candidate`, `insert_decision`, etc.) or `scripts/db_writer.py`'s buffer for batched multi-row writes within one script invocation — never hand-write INSERT/UPDATE statements.
- `--db-path <file>` on any `trader_query.py` subcommand points at a scratch DB for dry-runs — never needed in normal use.

## Need a query this doesn't cover?

Don't hand-write SQL or try to patch `scripts/trader_db.py`/`trader_query.py` yourself — `scripts/*.py` changes are `review_required`, not self-commit (see `skills/evolution-proposals.md`). Open a workboard card describing what you actually need it for; the orchestrator triages (is this actually necessary, not just "would be nice") before dispatching it to get built.
