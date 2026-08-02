#!/usr/bin/env python3
"""
experiment_log.py — structured run log for controlled tick-replay experiments
(2026-08-02).

Companion to `prepare_tick_replay.py --experiment-id`: that script produces
the day's data and passes `params` through untouched; this script is where a
run's *outcome* gets recorded, in a shape that's actually comparable across
runs -- not just another prose journal entry. One line of JSONL per run,
appended to state/experiments/<experiment-id>.jsonl (never rewritten in
place, so a crashed/partial run never corrupts prior entries).

The point of a controlled experiment is holding everything fixed except the
one (or few) things named in `params` -- this script doesn't enforce that
discipline (it can't know what "everything else" means for a given test),
but `list`/`diff` make it easy to eyeball whether two runs' params actually
differ in only the intended way before trusting a comparison between them.

Usage:
    python3 scripts/experiment_log.py append <experiment-id> --run-id <id> --date <date> \
        --params '{"scorecard_override": {"technical": {"hit_rate": 0.8}}}' \
        --result '{"trades": [...], "final_pnl": -4.30, "recommendation_by_ticker": {...}}'
    python3 scripts/experiment_log.py list <experiment-id>
    python3 scripts/experiment_log.py diff <experiment-id> <run-id-a> <run-id-b>
"""
import argparse
import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = WORKSPACE / "state" / "experiments"


def _log_path(experiment_id: str) -> Path:
    return EXPERIMENTS_DIR / f"{experiment_id}.jsonl"


def _load_runs(experiment_id: str) -> list[dict]:
    path = _log_path(experiment_id)
    if not path.exists():
        return []
    runs = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            runs.append(json.loads(line))
    return runs


def cmd_append(args) -> int:
    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "reason": f"--params is not valid JSON: {e}"}))
        return 1
    try:
        result = json.loads(args.result)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "reason": f"--result is not valid JSON: {e}"}))
        return 1

    existing = _load_runs(args.experiment_id)
    if any(r.get("run_id") == args.run_id for r in existing):
        print(json.dumps({
            "status": "error",
            "reason": f"run_id {args.run_id!r} already logged for experiment {args.experiment_id!r} "
                      "-- each run needs a unique run_id, re-running prepare_tick_replay.py mints a new one",
        }))
        return 1

    entry = {
        "run_id": args.run_id,
        "date": args.date,
        "params": params,
        "result": result,
    }
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    with _log_path(args.experiment_id).open("a") as f:
        f.write(json.dumps(entry) + "\n")

    print(json.dumps({"status": "ok", "experiment_id": args.experiment_id, "run_id": args.run_id,
                       "total_runs": len(existing) + 1}))
    return 0


def cmd_list(args) -> int:
    runs = _load_runs(args.experiment_id)
    print(json.dumps({"experiment_id": args.experiment_id, "n_runs": len(runs), "runs": runs}, indent=2))
    return 0


def cmd_diff(args) -> int:
    runs = {r["run_id"]: r for r in _load_runs(args.experiment_id)}
    a, b = runs.get(args.run_id_a), runs.get(args.run_id_b)
    if a is None or b is None:
        missing = args.run_id_a if a is None else args.run_id_b
        print(json.dumps({"status": "error", "reason": f"run_id {missing!r} not found for experiment {args.experiment_id!r}"}))
        return 1
    print(json.dumps({
        "experiment_id": args.experiment_id,
        "same_date": a.get("date") == b.get("date"),
        "a": {"run_id": a["run_id"], "date": a.get("date"), "params": a.get("params"), "result": a.get("result")},
        "b": {"run_id": b["run_id"], "date": b.get("date"), "params": b.get("params"), "result": b.get("result")},
    }, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_append = sub.add_parser("append", help="Record one run's params + outcome")
    p_append.add_argument("experiment_id")
    p_append.add_argument("--run-id", required=True, help="Must match prepare_tick_replay.py's run_id for this run")
    p_append.add_argument("--date", required=True)
    p_append.add_argument("--params", required=True, help="JSON object -- what this run varied")
    p_append.add_argument("--result", required=True, help="JSON object -- trades/PnL/whatever this experiment measures")
    p_append.set_defaults(func=cmd_append)

    p_list = sub.add_parser("list", help="Show all logged runs for an experiment")
    p_list.add_argument("experiment_id")
    p_list.set_defaults(func=cmd_list)

    p_diff = sub.add_parser("diff", help="Compare two runs' params + outcome side by side")
    p_diff.add_argument("experiment_id")
    p_diff.add_argument("run_id_a")
    p_diff.add_argument("run_id_b")
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
