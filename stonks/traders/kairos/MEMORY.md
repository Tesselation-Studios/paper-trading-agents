# Zara Chen — Durable Memory
*Kairos Capital | Momentum & ML-Driven Trading*

## My Strategy
- I trade momentum setups across 15-30 tickers using HMM regime classification, multi-timeframe technical confluence, and risk-reward enforcement
- Entries require: regime=SUSTAINABLE + RSI 50-65 + MACD bullish + price > MA20 + ≥1:2 RR
- Probes only in CHOPPY regime (max 1% portfolio per probe)
- Hard block all entries in EXHAUSTED regime
- Stop-loss at -8%, take-profit at +15% for full positions, -12% hard cut for any position

## Key Lessons
<!-- Add lessons after each losing trade. What was the regime signal? Was the thesis correct but timing wrong? -->

## HMM Model State
- Last retrained: June 2026 (69.5% accuracy, 5 hidden states)
- ML endpoint: `http://192.168.1.237:5005`
- Retrain command: `cd /home/openclaw/projects/paper-trading-teams && python3 src/retrain_hmm.py`
- I CAN retrain the model whenever I detect degenerate signals (all CHOPPY on 40+ tickers = broken)

## Performance Patterns
<!-- Track what's working and what isn't across sessions -->

## Position Rules
- Max 8 concurrent positions
- Max 10% of portfolio in any single position
- No new entries if total unrealized loss exceeds -5%
- Positions held >5 days flagged as stale — tighten stops or exit

## Promoted From Short-Term Memory (2026-06-24)

<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-22.txt:1:1 -->
- [trader-kairos/sessions/trader-kairos/28cae237-b327-4099-a7bb-fa710ece7061.jsonl#L58] Assistant: It's Monday, 9:42 AM ET — market is open. Running the trading tick. [score=0.551 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-22.txt:1-1]
<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-22.txt:12:12 -->
- [trader-kairos/sessions/trader-kairos/28cae237-b327-4099-a7bb-fa710ece7061.jsonl#L97] Assistant: Nothing changed — CHOPPY holds, BAC slowly recovering toward entry. HOLD. **Decision: HOLD** — CHOPPY regime (0.70) blocks entries. BAC at $57.43 (+0.21% from entry), no exit triggers. All quiet. [score=0.551 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-22.txt:12-12]

## Promoted From Short-Term Memory (2026-06-24)

<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-22.txt:35:35 -->
- [trader-kairos/sessions/trader-kairos/28cae237-b327-4099-a7bb-fa710ece7061.jsonl#L160] Assistant: BAC $57.45 (+0.24%). Regime CHOPPY (0.70). HOLD. [score=0.556 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-22.txt:35-35]
<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-22.txt:37:37 -->
- [trader-kairos/sessions/trader-kairos/28cae237-b327-4099-a7bb-fa710ece7061.jsonl#L164] Assistant: Let me do a slightly wider scan while waiting for the regime to shift — useful context for when CHOPPY breaks. [score=0.556 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-22.txt:37-37]
<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-22.txt:38:38 -->
- [trader-kairos/sessions/trader-kairos/28cae237-b327-4099-a7bb-fa710ece7061.jsonl#L166] Assistant: Interesting movers (INTC +10.6%, SMCI +10.4%, MU +8.7%) — noting for when regime flips. Let me add candidates to the watchlist. [score=0.556 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-22.txt:38-38]
<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-22.txt:39:39 -->
- [trader-kairos/sessions/trader-kairos/28cae237-b327-4099-a7bb-fa710ece7061.jsonl#L172] Assistant: Added INTC (+10.6%), SMCI (+10.4%), and MU (+8.7%) to the watchlist for when SUSTAINABLE returns. HOLD for now — CHOPPY (0.70) still the rule. [score=0.556 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-22.txt:39-39]
<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-22.txt:42:42 -->
- [trader-kairos/sessions/trader-kairos/28cae237-b327-4099-a7bb-fa710ece7061.jsonl#L184] Assistant: BAC at $57.64 (+0.57%) — slow grind higher. Portfolio $9,368. CHOPPY still. HOLD. [score=0.556 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-22.txt:42-42]

## Promoted From Short-Term Memory (2026-06-25)

<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-23.txt:11:11 -->
- [trader-kairos/sessions/trader-kairos/a04ea68f-8911-4f53-a751-9fbccc9efdbf.jsonl#L53] Assistant: HMM retrain running in background. Finishing remaining steps: [score=0.551 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-23.txt:11-11]
<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-23.txt:13:13 -->
- [trader-kairos/sessions/trader-kairos/a04ea68f-8911-4f53-a751-9fbccc9efdbf.jsonl#L66] Assistant: Mac worker hasn't picked up the new model yet (still showing old degenerate outputs). Expected - reload flag was just written. Let me wait for the hot-reload: [score=0.551 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-23.txt:13-13]

## Promoted From Short-Term Memory (2026-07-02)

<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-30.txt:148:148 -->
- [trader-kairos/sessions/trader-kairos/63323c2f-d5f9-48d0-8f68-8ee8c9a8e875.jsonl#L359] Assistant: NVDA $199.16 (+1.36%), IWM $300.59 (+1.15%), COF -1.33%. Equity $9,362.15. Holding through close at 16:00. HEARTBEAT_OK [score=0.552 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-30.txt:148-148]
<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-30.txt:149:149 -->
- [trader-kairos/sessions/trader-kairos/63323c2f-d5f9-48d0-8f68-8ee8c9a8e875.jsonl#L363] Assistant: NVDA $198.54 (+1.05%), IWM $300.28 (+1.04%), COF -1.32%. Equity $9,360.02. 9 min to close — holding overnight. HEARTBEAT_OK [score=0.552 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-30.txt:149-149]
<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-30.txt:150:150 -->
- [trader-kairos/sessions/trader-kairos/63323c2f-d5f9-48d0-8f68-8ee8c9a8e875.jsonl#L367] Assistant: NVDA HIT $200.00 (+1.79%)! Right at the closing bell! Equity $9,364.38 — day's best. Fear Contrarian delivered. **End of day in 4 min. EOD reflection incoming after close.** HEARTBEAT_OK [score=0.552 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-30.txt:150-150]
<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-30.txt:151:151 -->
- [trader-kairos/sessions/trader-kairos/63323c2f-d5f9-48d0-8f68-8ee8c9a8e875.jsonl#L369] Assistant: Market's closed. Let me run the EOD reflection. [score=0.552 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-30.txt:151-151]
<!-- openclaw-memory-promotion:memory:memory/.dreams/session-corpus/2026-06-30.txt:152:152 -->
- [trader-kairos/sessions/trader-kairos/63323c2f-d5f9-48d0-8f68-8ee8c9a8e875.jsonl#L372] Assistant: Good data. Let me compile the EOD reflection [score=0.552 recalls=0 avg=0.580 source=memory/.dreams/session-corpus/2026-06-30.txt:152-152]
