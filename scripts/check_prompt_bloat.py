#!/usr/bin/env python3
"""Flag prompt/instruction files that have grown or accumulated dated
annotations, so weekly review can trim them.

Scope: tick_prompt.md, worldview_sync_prompt.md, queue_drain_prompt.md,
skills/*.md — files an agent re-reads every tick/run. Explicitly NOT
journal/, off_hours/, research/, learning/, discoveries/, memory/,
DREAMS.md, MEMORY.md, HEARTBEAT.md, experience.json — those are logs,
meant to accumulate.

Two checks, both mechanical:
  1. Line count vs. the last recorded baseline (state/prompt_bloat_baseline.json)
     -- flags anything that grew.
  2. Dated-annotation patterns like "(2026-07-31" or "as of 2026-" -- these
     should never appear in a prompt file (see MEMORY: prompt writing style).

Usage:
    python3 scripts/check_prompt_bloat.py            # report + update baseline
    python3 scripts/check_prompt_bloat.py --no-update # report only
"""

import argparse
import json
import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
BASELINE_FILE = WORKSPACE / "state" / "prompt_bloat_baseline.json"

DATE_PATTERN = re.compile(r"\(20\d{2}-\d{2}-\d{2}|as of 20\d{2}-\d{2}-\d{2}")

TARGET_GLOBS = [
    "tick_prompt.md",
    "worldview_sync_prompt.md",
    "queue_drain_prompt.md",
    "skills/*.md",
]


def target_files():
    files = []
    for pattern in TARGET_GLOBS:
        files.extend(sorted(WORKSPACE.glob(pattern)))
    return files


def load_baseline() -> dict:
    if BASELINE_FILE.exists():
        try:
            return json.loads(BASELINE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_baseline(data: dict):
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-update", action="store_true", help="Report only, don't update baseline")
    parser.add_argument("--growth-threshold-pct", type=float, default=15.0)
    args = parser.parse_args()

    baseline = load_baseline()
    new_baseline = dict(baseline)
    flagged = []

    for f in target_files():
        rel = str(f.relative_to(WORKSPACE))
        text = f.read_text()
        lines = text.splitlines()
        line_count = len(lines)

        dated_hits = [ln for ln in lines if DATE_PATTERN.search(ln)]

        prev = baseline.get(rel)
        growth_pct = None
        if prev:
            prev_lines = prev.get("line_count", line_count)
            if prev_lines > 0:
                growth_pct = round((line_count - prev_lines) / prev_lines * 100, 1)

        reasons = []
        if dated_hits:
            reasons.append(f"{len(dated_hits)} dated annotation(s)")
        if growth_pct is not None and growth_pct >= args.growth_threshold_pct:
            reasons.append(f"grew {growth_pct}% since last check ({prev.get('line_count')} -> {line_count} lines)")

        if reasons:
            flagged.append({"file": rel, "line_count": line_count, "reasons": reasons, "dated_lines": dated_hits})

        new_baseline[rel] = {"line_count": line_count}

    print(json.dumps({"flagged": flagged, "files_checked": len(target_files())}, indent=2))

    if not args.no_update:
        save_baseline(new_baseline)


if __name__ == "__main__":
    main()
