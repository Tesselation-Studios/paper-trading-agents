# Proposal: Re-promote v1.1: MACDh-flip exit + regime-gated entries

**Status**: escalated
**Tier**: auto
**Created**: 2026-07-23T05:31:20.521873+00:00
**Files changed**: strategy.md

## Rationale

Split-window backtest (200d, 11 tickers: CHWY/CLF/DVN/F/GME/KHC/NVDA/OPEN/RKT/SNAP/WSC) shows v1.1 as the ONLY variant with positive Sharpe in both halves independently (1.647 first / 0.823 second). v1.0 (live v1.3.1) fails: 1.041 first / -1.306 second. v1.2 fails: 0.192 first / -0.006 second. Full window: v1.1 Sharpe 0.916, Sortino 1.501, Return +1.45% vs v1.0 Sharpe 0.153, Return +0.34%. Sweep confirms no parameter-only change helps — all 25 combos robust:false. Prior v1.3 revert cited v1.0 beating v1.1 on raw return 2/3 nights with different tickers — split-window Sharpe is the bar, and v1.1 now clears it.

## Evidence

split-window: v1.1 Sharpe H1=1.647 H2=0.823 (both positive), full=0.916. v1.0 H1=1.041 H2=-1.306 (fails). v1.2 H1=0.192 H2=-0.006 (fails). Sweep: 0/25 robust. Tickers: CHWY,CLF,DVN,F,GME,KHC,NVDA,OPEN,RKT,SNAP,WSC. 200d lookback, 2026-07-23 run.

## Resolution

escalated at 2026-07-23T05:32:54.767099+00:00

**Manual override note (Claude Code, 2026-07-23):** This is tier-auto by the mechanized rule (strategy.md-only), and the evidence is real and independently confirms what I found earlier tonight. Marking escalated instead of letting it auto-apply, because I told Raf directly I'd hold the v1.1 decision for his explicit sign-off before touching strategy.md — that commitment predates this proposal and should win. Raf: this is the same recommendation from earlier, now independently confirmed by the automated pipeline. Say the word and I'll apply it.
