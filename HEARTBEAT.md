tasks:

- name: daily-learning
  interval: 24h
  prompt: >
    Summarize the last day's entries. Read today's journal + active.md.
    Extract: lessons learned, errors made, whether prompts/skills/params made a difference.
    Write to learning/YYYY-MM-DD.md with structured YAML (lessons, errors,
    prompts_assessment, params_assessment, changes_summary).

- name: daily-file-trim
  interval: 24h
  prompt: >
    Audit agent files for bloat. Archive stale skills (unreferenced >7d),
    old journals (>14d), closed position theses (>3d closed), orphans.
    Write one-line trim summary. Commit all changes.

- name: weekly-patterns
  interval: 7d
  prompt: >
    Read all learning/ entries from the past 7 days + journals.
    Find overall patterns in summaries, errors, and lessons.
    Decide how to harden recurring issues into params, code, or prompt changes.
    Write to learning/weekly-patterns-YYYY-MM-DD.md.

# If nothing needs attention after all due tasks, reply HEARTBEAT_OK
