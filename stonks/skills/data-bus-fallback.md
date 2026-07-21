# Skill: Data Bus (best-effort context only, never load-bearing)

```bash
curl -s http://localhost:5000/quotes?symbols=SPY,AAPL,SOFI,PLTR | python3 -m json.tool
curl -s http://localhost:5000/sentiment?symbols=SOFI,NVDA | python3 -m json.tool
curl -s http://localhost:5000/momentum | python3 -m json.tool
curl -s http://localhost:5000/fear_greed | python3 -m json.tool
curl -s http://localhost:5000/macro | python3 -m json.tool
curl -s http://localhost:5000/risk | python3 -m json.tool
```

If any of these are down or return stale timestamps, note it in `active.md` and proceed without them. This service has been the recurring cause of stale-data outages across prior builds (paper-trading-teams, paper-trading-rebuild postmortems) — never let a tick stall waiting on it. `scripts/executor.py` (direct Alpaca) is always the source of truth for cash/positions/P&L, never this.
