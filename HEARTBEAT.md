# Rebuild Heartbeat

Spec-driven rebuild sprint. Board #3 must be empty by Sunday night.

## Tick Actions
1. Check board #3 Ready issues → claim them
2. Spawn max 2 coders for Ready items
3. Review open PRs against rebuild branch
4. Merge green PRs; spawn fix coders on failure
5. Post phase progress + blockers to Canvas

## Phase Order
1a. Test harness → 1b. Config system → 3. Learning loop → 2. Risk → 4. Nightly → 5. CI

## Rules
- Don't wait for PR review before starting next task
- Parallel fix coder + feature coder on test failure
- Traders test each phase as it lands
- Post-mortem after >2 attempts to fix same bug
- Every commit references issue number