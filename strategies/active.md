# Current Playbook — Stonks 🚀 TUE JUL 21, 2026 — 12:43 ET

## TUE 12:43 ET — Market Tick
- **Regime**: CHOPPY/FEAR (sentiment blind Day 14). PV $10,418, cash $8,166, 14 pos, no trades.
- **FUBO MACDh**: +0.0153 rising (no flip) → HOLD. Price $9.45 (-4.83% UPL).
- **SOFI**: $17.34, above $17 hard support → HOLD.
- **NVDA**: $205.32 < $207.63 entry → trim condition NOT met. HOLD.
- **MARA top mover**: +6.85%, nearest to 12% profit target. GME/LYFT/OPEN soft.
- **No stop breaches, no targets hit, no MACDh flips.** All 14 HOLD. Regime gates new entries.
- **Off-hours backtest note**: v1.2 underperforming v1.0 on corrected 200d/22-ticker run. Tonight's evolve: reconsider triple-confirmation entry rules.

## After-Market Close Positions (Mon Jul 20 reference)

## Session Summary
- **Regime**: CHOPPY/FEAR (F&G ~29). Sentiment blind Day 13.
- **Portfolio**: ~$10,454 | 14 positions | No stops hit
- **Star**: CHWY +5.40% 🟢
- **Struggles**: FUBO -5.14% 🟡, SOFI -4.38% 🟡
- **Paper API**: 3.5hr outage 9:30-13:00, recovered.

## After-Market Close Positions
| Ticker | Qty | Entry | Close | UPL% | Call |
|--------|-----|-------|-------|------|------|
| CHWY | 19 | $20.86 | $21.99 | +5.40% | HOLD — running well |
| NVDA | 5 | $207.63 | $203.33 | -2.07% | HOLD — above $200, Apple AI catalyst |
| AMC | 2 | $2.37 | $2.45 | +3.80% | HOLD |
| SOFI | 6 | $17.79 | $17.02 | -4.38% 🟡 | **WATCH** — $17 support |
| FUBO | 3 | $9.93 | $9.42 | -5.14% 🟡 | **WATCH** — MACD check open |
| GME | 10 | $22.38 | $21.76 | -2.87% | HOLD |
| KHC | 3 | $25.95 | $25.86 | -0.36% | HOLD |
| F | 2 | $14.13 | $14.01 | -0.88% | HOLD — TRENDING_UP |
| LYFT | 19 | $15.67 | $15.43 | -1.79% | HOLD |
| MVST | 5 | $0.90 | $0.90 | +0.18% | HOLD — stop $0.80 |
| DJT | 1 | $9.79 | $9.72 | -0.82% | HOLD |
| OPEN | 2 | $4.50 | $4.45 | -1.33% | HOLD |
| SNAP | 1 | $4.58 | $4.57 | -0.22% | HOLD |
| MARA | 3 | $11.83 | $11.65 | -1.52% | HOLD |

## TUE 02:37 ET — Off-Hours Tick
- Regime: CHOPPY/FEAR holding. Market closed. PV $10,424, cash $8,166, 14 pos.
- No stop breaches. SOFI $17.10 (-3.88%) nearing $17 support. FUBO $9.53 (-4.04%).
- WATCH: SNAP, MARA added late Mon. Watchlist healthy (10 candidates).
- Decision: HOLD all. No trades outside market hours.

## Tuesday Game Plan
0. **🚨 READ `off_hours/2026-07-21.md` before tonight's evolve**: replay_check.py had a bug (macd_hist was actually reading the signal line) — fixed. Corrected 200d/22-ticker backtest shows v1.1 AND v1.2 both underperforming v1.0. v1.2 (currently live) is the worst performer on the largest sample tested yet (170 trades). Second night running v1.1 has looked weak in backtest. Worth treating v1.2's promotion as unsettled.
1. **First thing**: Check FUBO MACDh — flip = SELL immediately
2. **SOFI**: $17 hard support. Break = cut immediately
3. **CHWY**: Let run, consider trim at $23+ (profit target zone)
4. **New entries**: Gated — regime still CHOPPY/FEAR, sentiment blind
5. **Sentiment pipeline**: Off-hours root cause investigation needed 🚨

## 🌙 Nightly Action Items (Jul 20)
- 🚨 **ESCALATE**: Sentiment pipeline Day 13 blind — operator fix needed
- 📐 **Trim NVDA**: Sell 2 shares Tue open if > $207.63 entry (6% cap = 3 shares max)
- 🧹 **Watchlist feed**: Pruned HOOD/COIN. Need 3+ new sub-$15 names by Tue EOD
- 📝 **Homework**: Pick ONE (trailing stops / FUBO stats / NVDA backtest) and resolve
- ⏰ **Monday pattern**: Set up before 9:25 ET — Paper API fragile on Mondays

## Key Levels
- **SOFI support**: $17 (hard cut line)
- **FUBO MACDh**: Determining factor — check at open
- **NVDA floor**: $200
- **CHWY target zone**: $23.36 (12% from $20.86)