#!/usr/bin/env python3
"""
Mirror journal/YYYY-MM-DD.md into memory/journal/YYYY-MM-DD.md so OpenClaw's
memory-core indexer and short-term recall/dreaming pipeline can actually see
Stan's daily journal content.

Root cause this fixes (found 2026-07-25): OpenClaw's short-term recall store
only ever records a memory_search hit if the hit's path matches
memory/<subdir>/YYYY-MM-DD.md (or a bare YYYY-MM-DD.md basename) — see
short-term-promotion-*.js's isShortTermMemoryPath/SHORT_TERM_PATH_RE. Stan's
memory/ directory only ever had .dreams/ and dreaming/ subdirs, never any
dated notes, so every memory_search hit landed on MEMORY.md instead (which is
explicitly excluded from recall tracking — it's already-durable content, not
a promotion candidate). Result: dreaming's minRecallCount>=1 gate could never
be satisfied, no matter how many times tick_prompt.md called memory_search.

Idempotent and safe to run every time: only (re)writes a memory/journal/*.md
file when it's missing or the source journal file changed (mtime+size check,
avoids needless reindex churn). Never deletes anything from journal/ (source
of truth stays in journal/, this is a read-only mirror).
"""
from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_DIR = REPO_ROOT / "journal"
MEMORY_MIRROR_DIR = REPO_ROOT / "memory" / "journal"


def sync() -> list[str]:
    MEMORY_MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    synced = []
    for src in sorted(JOURNAL_DIR.glob("*.md")):
        dst = MEMORY_MIRROR_DIR / src.name
        if dst.exists():
            src_stat, dst_stat = src.stat(), dst.stat()
            if src_stat.st_mtime <= dst_stat.st_mtime and src_stat.st_size == dst_stat.st_size:
                continue
        shutil.copy2(src, dst)
        synced.append(src.name)
    return synced


if __name__ == "__main__":
    changed = sync()
    if changed:
        print(f"synced {len(changed)} journal file(s) into memory/journal/: {', '.join(changed)}")
    else:
        print("memory/journal/ already up to date, nothing to sync")
