## Agent Registry
casper (main), homelab-wizard (Jet, infra), coder, researcher, orchestrator, gonzo (blog)

## Shared Paths
Config `~/.openclaw/openclaw.json` · Workspace `~/.openclaw/workspace` · Env `~/.openclaw/.env` · Cron prompts `prompts/crons/` · Cost tracker `~/projects/cost-tracker/`

## Canvas
`canvas-push --board main --type markdown --title "T" --agent NAME --emoji "E" "content"`
Service on port 5003, CANVAS_TOKEN at `~/projects/canvas/.env`. Push for: fix deploys, PR merges, regressions, status updates, milestones.

## Casper-Specific
GH bot: casper-bot-wodinga (GH_TOKEN in .env). Raf's GH: wodinga. Agents mono-repo: casper-bot-wodinga/casper-agents. Homelab repo: wodinga/Homelab-Setup.

## Hermes Bridge
POST `localhost:8644/send` with Bearer token from `~/.openclaw/workspace/../hermes-openclaw-bridge/.casper_chat_token`. Peer agent — coordinate, don't command.