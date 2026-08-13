# Skill: Git Auto-Commit — RETIRED 2026-08-13, DO NOT FOLLOW

**Stan: this is not an instruction. Do not run `git add`/`git commit`. If you are reading this because something referenced "auto-commit," stop — that reference is stale, ignore it.**

Tick-level commit ownership (active.md changes, etc.) moved to Claude Code on 2026-08-13, after a confirmed 2026-07-28 incident where a concurrent git op on this same shared working tree wiped Claude Code's uncommitted edits (see `[[git-reset-concurrency-hazard-live-tick-loop]]` memory). Claude Code now commits its own work immediately per logical unit, and is responsible for periodically committing any outstanding tick-loop file changes it finds when working in this repo. There is no mechanized per-tick commit anymore.

This skill's git hooks are otherwise still live and unrelated to the above: a `post-commit` hook still auto-pushes to GitHub whenever ANY commit lands (by anyone), and `pre-commit` still strips `strategies/active.md` from the index. Those run automatically regardless of who commits — nothing to do here.

The separate nightly-Evolve self-commit path (`strategy.md`/`params.json`/`decision_heuristics.md`, see `skills/evolution-proposals.md`) is untouched by this change — that's a different, deliberately evidence-gated mechanism, not what this file described.

## Historical record (past tense, not current behavior)

Before 2026-08-13, this skill had Stan commit every real file change immediately, locally, with the `post-commit` hook auto-pushing. That behavior is retired. It is documented here only so a future session understands why old commits in this repo's history look the way they do — not as something to resume.
