# Skill: Evolution Proposals

Structured version of what nightly Evolve already does informally. Two tiers, decided by which files a change touches — never by who's asking:

- **auto** — `strategy.md`/`params.json` only. Self-commit directly during nightly Evolve, exactly as today (version bump, journal rationale, git push). No proposal file needed for this tier — it's not new behavior, just documenting that it stays unreviewed by design (same bar as always: real backtest evidence, e.g. `replay_check.py --split-window` or `scripts/replay_check.py`'s `sweep_thresholds()`, Sharpe-positive in both halves, not one aggregate number).
- **review_required** — anything touching `scripts/*.py` (guardrail gates/code), `TOOLS.md`/`HEARTBEAT.md`/`AGENTS.md`, or any other doc/tooling change. Write a proposal instead of self-committing:

```bash
python3 scripts/evolution_proposal.py create \
  --title "Add earnings-day gate" \
  --rationale '...' \
  --files scripts/executor.py \
  --evidence "5 journal entries this month show losses clustering around earnings dates"
```

`openclaw.json` (agent registry — tools, model, heartbeat) is never a valid target at all — `evolution_proposal.py` rejects it outright. Propose those changes to Raf directly (Telegram/conversation), not through this pipeline.

**Why the split**: a bad guardrail is worse than a slow one (same judgment `rule-mechanization-audit.md` already uses) — code/tooling changes need real engineering verification a 30-minute nightly window can't provide. Strategy/param tuning is lower-stakes and faster-moving by design — this is also how "loosen fast during the early track-record phase, not just tighten" actually gets to happen without waiting on a human every time.

**stonks-evolution-batch** (cron, agent `trader-stonks`, off-hours) runs `universe_scan.py` + `replay_check.py --split-window` + `sweep_thresholds()` and writes a proposal — `auto`-tier for anything that's just a strategy/param change clearing the evidence bar, `review_required` for anything else it notices (including tooling/testing improvement ideas — same mechanism, not a separate system). It also runs `bankroll.py --competition` (tier/expectancy-trend/days-to-deadline) and folds that into a proposal's rationale as context — informational, doesn't change the Sharpe/split-window evidence bar above.

**stonks-evolution-review** (cron, agent `main`, after the batch) reads open proposals via `python3 scripts/evolution_proposal.py list --status open`: applies `auto`-tier directly and marks `resolved`; for `review_required` sends one Telegram summary to Raf AND creates a workboard card (`openclaw workboard create "<proposal title>" --labels evolution-proposal --priority normal --notes "<rationale/evidence summary + proposal file path>"`), then marks the proposal `resolved --resolution escalated` — the proposal file stays as the durable evidence record, the workboard card is what makes it trackable/claimable instead of resting on someone noticing a chat message. 2026-08-12: added after `deployment_pressure_override_v0`, a 7-period-RSI idea, and a re-screening process gap all sat Telegram-escalated for weeks with no forcing function to close them out — do not self-clear the card, that defeats the point same as the proposal itself.
