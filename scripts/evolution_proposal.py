#!/usr/bin/env python3
"""
Evolution Proposal — structured, trackable version of what Stonks's
nightly Evolve step already does informally (edit strategy.md/params.json
with a journal rationale). Two things this adds that prose alone doesn't:

1. Tier is DERIVED from which files a proposal touches, not asserted by
   whoever writes it — strategy.md/params.json-only changes are "auto"
   (mirrors what already happens today, unreviewed, via nightly Evolve);
   anything touching scripts/*.py (guardrail gates/code) or
   TOOLS.md/HEARTBEAT.md/AGENTS.md is "review_required" — matches
   skills/rule-mechanization-audit.md's existing judgment that a bad
   guardrail is worse than a slow one. openclaw.json is never a valid
   target at all — rejected outright, not just gated.

2. A durable, git-tracked file (proposals/YYYY-MM-DD-<slug>.md) instead
   of a one-off Telegram ping, so stonks-evolution-review (a cron on the
   `main` agent, not a new persistent agent) has something concrete to
   read, apply, or escalate — same write-file-then-mechanically-consume
   pattern already proven by discoveries/*.md -> merge_discoveries.py.

Usage:
    python3 scripts/evolution_proposal.py create --title "..." \\
        --rationale "..." --files strategy.md,params.json --evidence "..."
    python3 scripts/evolution_proposal.py list [--status open]
    python3 scripts/evolution_proposal.py resolve <path> --resolution applied
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
PROPOSALS_DIR = REPO_ROOT / "proposals"

AUTO_TIER_FILES = {"strategy.md", "params.json"}
FORBIDDEN_FILES = {"openclaw.json"}


class ForbiddenProposalTarget(ValueError):
    pass


def classify_tier(files_changed: List[str]) -> str:
    """'auto' only if EVERY file touched is strategy.md/params.json —
    otherwise 'review_required'. Any file whose name matches
    FORBIDDEN_FILES raises rather than being classified at all; those
    changes get proposed to a human/Claude-Code directly (Telegram/
    conversation), never through this file-based pipeline.
    """
    names = {Path(f).name for f in files_changed}
    if names & FORBIDDEN_FILES:
        raise ForbiddenProposalTarget(
            f"{names & FORBIDDEN_FILES} can never be an evolution-proposal target — "
            f"propose changes to openclaw.json to a human directly, not via this pipeline"
        )
    if names and names <= AUTO_TIER_FILES:
        return "auto"
    return "review_required"


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "proposal"


def write_proposal(title: str, rationale: str, files_changed: List[str], evidence: str,
                    proposals_dir: Path = PROPOSALS_DIR) -> Path:
    tier = classify_tier(files_changed)  # raises ForbiddenProposalTarget before any write
    proposals_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    filename = f"{now.date().isoformat()}-{slugify(title)}.md"
    path = proposals_dir / filename
    if path.exists():
        path = proposals_dir / f"{now.date().isoformat()}-{slugify(title)}-{now.strftime('%H%M%S')}.md"

    content = f"""# Proposal: {title}

**Status**: open
**Tier**: {tier}
**Created**: {now.isoformat()}
**Files changed**: {", ".join(files_changed)}

## Rationale

{rationale}

## Evidence

{evidence}
"""
    path.write_text(content)
    return path


def _parse_proposal(path: Path) -> Dict[str, Any]:
    text = path.read_text()
    title_match = re.search(r"^# Proposal:\s*(.+)$", text, re.MULTILINE)
    status_match = re.search(r"^\*\*Status\*\*:\s*(.+)$", text, re.MULTILINE)
    tier_match = re.search(r"^\*\*Tier\*\*:\s*(.+)$", text, re.MULTILINE)
    created_match = re.search(r"^\*\*Created\*\*:\s*(.+)$", text, re.MULTILINE)
    files_match = re.search(r"^\*\*Files changed\*\*:\s*(.+)$", text, re.MULTILINE)
    return {
        "path": str(path),
        "title": title_match.group(1).strip() if title_match else None,
        "status": status_match.group(1).strip() if status_match else None,
        "tier": tier_match.group(1).strip() if tier_match else None,
        "created": created_match.group(1).strip() if created_match else None,
        "files_changed": [f.strip() for f in files_match.group(1).split(",")] if files_match else [],
    }


def list_proposals(status: str = None, proposals_dir: Path = PROPOSALS_DIR) -> List[Dict[str, Any]]:
    if not proposals_dir.is_dir():
        return []
    results = []
    for path in sorted(proposals_dir.glob("*.md")):
        parsed = _parse_proposal(path)
        if status is None or parsed["status"] == status:
            results.append(parsed)
    return results


def mark_resolved(path: Path, resolution: str) -> None:
    """resolution: e.g. 'applied' or 'escalated' — updates Status in place
    and appends a timestamped resolution note. Never deletes the proposal
    file; it's the audit trail."""
    path = Path(path)
    text = path.read_text()
    text = re.sub(r"^\*\*Status\*\*:\s*.+$", f"**Status**: {resolution}", text, count=1, flags=re.MULTILINE)
    now = datetime.now(timezone.utc).isoformat()
    text += f"\n## Resolution\n\n{resolution} at {now}\n"
    path.write_text(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--title", required=True)
    create.add_argument("--rationale", required=True)
    create.add_argument("--files", required=True, help="comma-separated file list")
    create.add_argument("--evidence", required=True)

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--status", default=None)

    resolve = sub.add_parser("resolve")
    resolve.add_argument("path")
    resolve.add_argument("--resolution", required=True, choices=["applied", "escalated", "rejected"])

    args = parser.parse_args()

    if args.command == "create":
        try:
            path = write_proposal(
                title=args.title, rationale=args.rationale,
                files_changed=[f.strip() for f in args.files.split(",")],
                evidence=args.evidence,
            )
        except ForbiddenProposalTarget as e:
            print(f"REJECTED: {e}")
            return 1
        print(f"Created: {path}")
        return 0

    if args.command == "list":
        import json
        print(json.dumps(list_proposals(status=args.status), indent=2))
        return 0

    if args.command == "resolve":
        mark_resolved(Path(args.path), args.resolution)
        print(f"Marked {args.resolution}: {args.path}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
