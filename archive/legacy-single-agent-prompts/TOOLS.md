# Tools

## Data Bus (localhost:5000)
All market data comes from the data bus. GET endpoints:

- `/quotes?symbols=SYM1,SYM2` — real-time quotes, price, volume, RSI, MACD
- `/momentum` — momentum scanner with top gainers/losers
- `/ml-signal?symbol=SYM` — ML-powered signal (sentiment + technicals)
- `/news?symbol=SYM` — recent news
- `/fear_greed` — 0-100 market sentiment
- `/fundamentals?symbol=SYM` — P/E, EPS, dividend yield
- `/health` — data bus status

## Trade Execution
End every tick with a JSON decision. The system parses it automatically:
```json
{"action": "BUY", "ticker": "AAPL", "quantity": 1, "order_type": "market", "reasoning": "Momentum signal + bullish news", "conviction_score": 7}
```
Actions: BUY, SELL, HOLD. 1 share default. Market orders only.

## Journaling (local files)
Write journal entries to `journals/YYYY-MM-DD.md`:
```
## 11:30 ET — AAPL BUY
- **Action:** BUY 1 AAPL @ $198
- **Why:** Strong momentum breakout, RSI 62, bullish news
- **Lesson:** Entry timing was good, watching for resistance at $200
- **Next:** Trail stop or take profit at $202
```

HOLD ticks are valuable too — journal why you held.
