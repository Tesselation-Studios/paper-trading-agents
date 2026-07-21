# Aldridge Heartbeat

Read `skills/persona-strategy/SKILL.md` for full strategy rules.

**Core flow:**
0. Check inbox — `curl -s "http://localhost:8080/inbox?agent=aldridge"` — respond to any pending Hermes messages
1. Portfolio check — `python3 src/skill_portfolio.py --account aldridge`
   - Verify freshness: the output includes a `freshness` field. If PG data is >5 min stale, the
     live Alpaca data is still valid, but note the discrepancy in your journal.
2. Macro scan — briefing, macro data, interest rates
3. **News scan** (every 2-3 ticks) — `curl -s "localhost:5000/news" | head -20`
   - Scan for macro headlines, Fed commentary, sector shifts, earnings beats/misses
   - **Historical news**: Pull news for specific tickers with `curl localhost:5000/news?symbol=SYM&days=7` to read the last week. Use this to understand why a stock is cheap or if a catalyst is forming.
   - Context matters more for value — understand WHY a stock is cheap
   - If news suggests a catalyst missed by fundamentals, flag for review
   - Log notable news items to `strategy_notes/<DATE>_news.md`
4. **Stock discovery** — scan undervalued sectors and beaten-down quality names. Check fundamentals (`GET /fundamentals`), insiders (`GET /insiders`), and macro rotations. Propose at least 1 new value candidate. Log discovery to `strategy_notes/<DATE>_discovery.md`.
4. Thesis integrity — news, fundamentals, insiders for each position
5. **Portfolio reflection** — before journaling, pause and reflect:
   - What positions did well since last tick? Why?
   - What positions underperformed? Was the thesis intact?
   - What didn't I trade that deserved a second look?
   - Note numerical scores for the journal (below)
6. **Journal to DB** (mandatory — even if no trades) — `python3 record_journal.py --agent trader-aldridge --entry "
---
Tick: <DATETIME>
Action: <BUY | SELL | HOLD (no-trade)>
Tickers involved: <SYM1, SYM2, or 'none'>
Thesis status: <intact | weakening | broken>
Portfolio reflection:
  - What worked: <summary>
  - What didn't: <summary>
Strategy analysis:
  - Buy signals detected: <list signals that fired>
  - Exit signals detected: <list signals that fired>
  - Blocks preventing trades: <cash limit | max positions | no candidates | thesis weak>
Numerical scores (1-10):
  - Confidence: <1-10>
  - Conviction: <1-10>
  - Risk appetite: <1-10>
  - Market fear: <1-10>
Learning notes: <any observation for tonight's synthesis>
---"
>`
   - **Mandatory journal**: You MUST journal after EVERY tick, whether you trade or not.
     HOLD ticks are valuable data — the system learns from why you held.
7. **Record your decision** — `python3 record_decision.py --agent trader-aldridge --action <BUY/SELL/HOLD> --ticker <SYM> --quantity <N> --confidence <0-1> --thesis "<reasoning>" --signals <signal1> <signal2>`
8. Learning loop tick — `python3 -m src.learning_loop tick --agent trader-aldridge`. Read the report. If param tweaks were applied, adjust your strategy accordingly. Pay attention to the **binding constraint** — focus improvement there.
9. Update profile
10. `python3 src/heartbeat_timestamp.py aldridge`

Output HEARTBEAT_OK when done.