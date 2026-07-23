# Skill: Workspace Review

Mechanized consistency check between `strategy.md`/`params.json`/`executor.py` — catches the same class of bug twice found by hand this week (stale params after a strategy revert, a strategy propagation lag nobody noticed for 2 hours). Ported from `paper-trading-rebuild`'s `validate_prompt_format.py`/`pre_market_gate.py` pattern.

```bash
python3 scripts/workspace_review.py --gate
```

- **Critical findings** (invalid `params.json`, missing required keys) write `state/.workspace_blocked` and mean **do not trade this tick**. Note the reason in `active.md`, `HEARTBEAT_OK`.
- **Warnings** (dead params, `guardrail_gates`↔`executor.py` drift, version mismatch) never block trading — surface them in `active.md`, don't silently ignore.
- `--gate --status` — check gate state without re-running checks. `--gate --clear` — manual override once a human/Claude-Code has actually fixed the underlying issue. **Don't self-clear mid-session** without the root cause actually being addressed — that defeats the point.
- Dead-param findings are a heuristic (substring match across `scripts/*.py` + live-consumed docs), not proof — a real finding worth a look, not an automatic delete. Escalate genuinely surprising ones the same way as `skills/rule-mechanization-audit.md`, don't fix guardrail-adjacent code yourself mid-tick.
