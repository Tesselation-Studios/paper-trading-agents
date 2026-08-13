# Skill: Git Auto-Commit (INACTIVE for the tick loop — 2026-08-13)

**No longer triggered from `tick_prompt.md`** — Raf moved tick-level commit ownership (active.md changes, etc.) to Claude Code, after a confirmed 2026-07-28 incident where a concurrent git op on this same shared working tree wiped Claude Code's uncommitted edits (see `[[git-reset-concurrency-hazard-live-tick-loop]]` memory). Claude Code now commits its own work immediately per logical unit, and is responsible for periodically committing any outstanding tick-loop file changes (active.md, positions, journal) it finds when working in this repo — there is no longer a mechanized per-tick commit.

This skill's mechanics/hooks are otherwise still live and unrelated to the above: the `post-commit` hook still auto-pushes directly to GitHub (`Tesselation-Studios/paper-trading-agents`, whatever branch is currently checked out — currently `v4`) whenever ANY commit lands, and `pre-commit` still strips `strategies/active.md` from the index either way. This file is kept for reference/history, not as an active tick instruction.

The separate nightly-Evolve self-commit path (`strategy.md`/`params.json`/`decision_heuristics.md`, see `skills/evolution-proposals.md`) is untouched by this change — that's a different, deliberately evidence-gated mechanism, not the tick-level commit this skill described.

---

Below is the original mechanism, preserved for reference:

Commit every file change immediately, locally — no approval needed. A `post-commit` hook auto-pushes directly to GitHub (`Tesselation-Studios/paper-trading-agents`, whatever branch is currently checked out — currently `v4`) — no mirror repo, no rsync, this workspace repo IS the source of truth. Push is the revert safety net (see `AGENTS.md` immutable section) — don't rely on local-only history.

```bash
cd ~/.openclaw/workspace-trader-stonks
git add -A
git commit -m "stonks: $(date +%Y-%m-%d) — [brief description]"
```

| Rule | Detail |
|---|---|
| Frequency | After every *real* file change, not batched |
| Empty commit | `git status` first — skip if nothing changed |
| Push | Automatic via hook — never add `--no-verify` or disable it |
| Message | Descriptive: "stonks: dropped AMC from watchlist, idle 24 ticks" |
| Grouping | One commit per logical change if multiple files touched |
| Binary files | small state files fine to commit — not logs. `state/trader.db` (bankroll/decisions/positions/watchlist state) is gitignored, not committed at all — one less source of noisy per-trade commits |

**Don't manufacture a change just to have something to commit**: position files are thesis storage, not a price mirror (see `tick_prompt.md` step 9 — price lives in Alpaca, not duplicated here). If the only thing different about a file since last commit is a live-derivable number or a timestamp, that's not a real change — don't write it, don't commit it.

**Why**: git history is the rollback mechanism — no human approves file edits, so a wrong change gets fixed by the next nightly reverting it, not by gatekeeping upfront. That only works if the history is signal, not noise — a real change buried in hundreds of price-drift commits is as hard to find as no history at all.
