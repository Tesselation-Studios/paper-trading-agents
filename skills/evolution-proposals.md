# Skill: Evolution Proposals

Structured version of what nightly Evolve already does informally. Two tiers, decided by which files a change touches — never by who's asking:

- **auto** — `strategy.md`/`params.json` only. Self-commit directly during nightly Evolve, exactly as today (version bump, journal rationale, git push). No proposal file needed for this tier — it's not new behavior, just documenting that it stays unreviewed by design (same bar as always: real backtest evidence, e.g. `replay_check.py --split-window` or `scripts/replay_check.py`'s `sweep_thresholds()`, Sharpe-positive in both halves, not one aggregate number).
- **review_required** — anything touching `scripts/*.py` (guardrail gates/code), `TOOLS.md`/`HEARTBEAT.md`/`AGENTS.md`, or any other doc/tooling change. Write a proposal instead of self-committing:

```bash
python3 scripts/evolution_proposal.py create \
  --title "Add earnings-day gate" \
  --rationale "..." \
  --files scripts/executor.py \
  --evidence "5 journal entries this month show losses clustering around earnings dates"
```

`openclaw.json` (agent registry — tools, model, heartbeat) is never a valid target at all — `evolution_proposal.py` rejects it outright. Propose those changes to Raf directly (Telegram/conversation), not through this pipeline.

**Why the split**: a bad guardrail is worse than a slow one (same judgment `rule-mechanization-audit.md` already uses) — code/tooling changes need real engineering verification a 30-minute nightly window can't provide. Strategy/param tuning is lower-stakes and faster-moving by design — this is also how "loosen fast during the early track-record phase, not just tighten" actually gets to happen without waiting on a human every time.

**stonks-evolution-batch** (cron, agent `trader-stonks`, off-hours) runs `universe_scan.py` + `replay_check.py --split-window` + `sweep_thresholds()` and writes a proposal — `auto`-tier for anything that's just a strategy/param change clearing the evidence bar, `review_required` for anything else it notices (including tooling/testing improvement ideas — same mechanism, not a separate system).

**stonks-evolution-review** (cron, agent `main`, after the batch) reads open proposals via `python3 scripts/evolution_proposal.py list --status open`: applies `auto`-tier directly and marks `resolved`; for `review_required` sends one Telegram summary to Raf and leaves it open for a human/Claude-Code session — do not self-clear, that defeats the point.
