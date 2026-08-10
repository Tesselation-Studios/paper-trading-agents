#!/usr/bin/env python3
"""
Strategy-version comparison via real LLM-judgment replay — compares how
Stan's ACTUAL judgment (not the hand-coded rules-engine proxy) would have
performed under different historical strategy.md versions, over the same
real historical data.

Built on top of two existing pieces, not a new replay engine:
  - llm_replay.py's make_llm_trader()/build_replay_prompt() — a real
    `openclaw agent` call per simulated day, strategy.md text injected as
    a parameter (never touches the live file on disk).
  - replay_check.py's fetch_history()/build_tick_stream()/
    split_ticks_by_midpoint()/compute_risk_metrics() — the same
    split-window Sharpe machinery strategy.md's promotion bar references
    (both halves positive independently, not one aggregate number).

Only strategy.md is varied per version, not params.json: the LLM replay
path (run_llm_replay/make_llm_trader) never reads params.json at all —
its only strategy-shaped input is the prose text embedded in the prompt,
and require_conviction/max_position_pct are replay-harness constants, not
live config. This is a judgment-only comparison by design (see the plan
this came from) — it does NOT model the Aug 5 scale-in-403 fix or the
sentiment-pipeline fix, since neither can be represented in a technicals-
only historical bar replay in the first place.

Cost note: each variant needs one real LLM call per simulated day for the
split-window check (first-half half-days + second-half half-days), same
total as one full-window pass -- not 2x. This script runs the FULL tick
stream first (populating make_llm_trader's per-day cache with one real
call per day), then re-runs the SAME trader closure over the first/second
half slices -- those hit the cache instead of re-invoking the agent,
unlike replay_check.sweep_thresholds's pattern of a fresh trader per half
(fine there, since a rules-engine trader is free to rebuild; wasteful
here, since an LLM trader is not).

Usage:
    python3 scripts/strategy_version_replay.py [--days N] [--tickers T ...]
    python3 scripts/strategy_version_replay.py --versions v1.0-baseline:2fd6500 current:HEAD
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_check  # noqa: E402
import llm_replay  # noqa: E402

from replay_harness import replay_trader  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# The 4 versions confirmed in the plan: a clean single-version state each,
# pinned to specific commits (not just the nearest version-bump commit --
# see the plan's note on strategy.md/params.json/executor.py drift).
DEFAULT_VERSIONS: List[Tuple[str, str]] = [
    ("v1.0-baseline", "2fd6500"),
    ("v1.7-scale-in", "3a9d3ee"),
    ("v1.13-macdh-removed", "32aca52"),
    ("current", "HEAD"),
]


def get_strategy_text_at_ref(ref: str) -> str:
    """git show <ref>:strategy.md -- read-only, no checkout/worktree, never
    touches the live working tree (same safety property run_llm_replay's
    strategy_text param already relies on)."""
    result = subprocess.run(
        ["git", "show", f"{ref}:strategy.md"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git show {ref}:strategy.md failed: {result.stderr.strip()}")
    return result.stdout


def run_variant_split_window(label: str, strategy_text: str, frames, ticks,
                              model: str) -> Dict[str, Any]:
    """Full-window + split-window replay for ONE strategy.md version,
    reusing a single llm_trader closure (and its per-day cache) across all
    three replay_trader calls so the split-window halves are free lookups,
    not fresh LLM calls -- see module docstring."""
    llm_trader = llm_replay.make_llm_trader(frames, strategy_text, model=model)

    full_result = replay_trader(ticks, llm_trader, initial_balance=10_000.0,
                                 max_position_pct=0.06, require_conviction=0.5)
    first_half, second_half = replay_check.split_ticks_by_midpoint(ticks)
    first_result = replay_trader(first_half, llm_trader, initial_balance=10_000.0,
                                  max_position_pct=0.06, require_conviction=0.5)
    second_result = replay_trader(second_half, llm_trader, initial_balance=10_000.0,
                                   max_position_pct=0.06, require_conviction=0.5)

    full_summary = replay_check.summarize(full_result, label)
    first_sharpe = replay_check.compute_risk_metrics(first_result)["sharpe"]
    second_sharpe = replay_check.compute_risk_metrics(second_result)["sharpe"]
    robust = bool(first_sharpe is not None and second_sharpe is not None
                  and first_sharpe > 0 and second_sharpe > 0)

    return {
        "version": label,
        "sharpe": full_summary["sharpe"],
        "first_half_sharpe": first_sharpe,
        "second_half_sharpe": second_sharpe,
        "robust": robust,
        "total_return_pct": full_summary["total_return_pct"],
        "max_drawdown_pct": full_summary["max_drawdown_pct"],
        "win_rate": full_summary["win_rate"],
        "n_trades": full_summary["n_trades"],
        "final_equity": full_summary["final_equity"],
    }


def run_comparison(versions: List[Tuple[str, str]], tickers: List[str],
                    lookback_days: int, model: str) -> Dict[str, Any]:
    original_lookback = replay_check.LOOKBACK_DAYS
    replay_check.LOOKBACK_DAYS = lookback_days
    try:
        frames = replay_check.fetch_history(tickers)
    finally:
        replay_check.LOOKBACK_DAYS = original_lookback

    if not frames:
        return {"error": "no usable history for any ticker"}
    ticks = replay_check.build_tick_stream(frames)

    results = []
    for label, ref in versions:
        try:
            strategy_text = get_strategy_text_at_ref(ref)
        except RuntimeError as e:
            results.append({"version": label, "ref": ref, "error": str(e)})
            continue
        row = run_variant_split_window(label, strategy_text, frames, ticks, model=model)
        row["ref"] = ref
        results.append(row)

    return {
        "tickers_used": sorted(frames.keys()),
        "lookback_days": lookback_days,
        "model": model,
        "versions": results,
    }


def print_comparison_table(comparison: Dict[str, Any]) -> None:
    if "error" in comparison:
        print(json.dumps(comparison, indent=2))
        return
    cols = ["version", "sharpe", "first_half_sharpe", "second_half_sharpe", "robust",
            "total_return_pct", "max_drawdown_pct", "win_rate", "n_trades"]
    widths = {c: max(len(c), 10) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("-" * len(header))
    for row in comparison["versions"]:
        if "error" in row:
            print(f"{row['version']}: ERROR — {row['error']}")
            continue
        line = "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in cols)
        print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=llm_replay.DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--model", default=llm_replay.DEFAULT_MODEL)
    parser.add_argument("--versions", nargs="*", default=None,
                         help="label:git_ref pairs, e.g. v1.0:2fd6500 current:HEAD. "
                              "Defaults to the 4 versions pinned in DEFAULT_VERSIONS.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of a table.")
    args = parser.parse_args()

    if args.versions:
        versions = []
        for spec in args.versions:
            if ":" not in spec:
                print(json.dumps({"error": f"--versions entries must be label:ref, got {spec!r}"}))
                return 1
            label, ref = spec.split(":", 1)
            versions.append((label, ref))
    else:
        versions = DEFAULT_VERSIONS

    tickers = args.tickers or replay_check.load_live_universe()
    comparison = run_comparison(versions, tickers, lookback_days=args.days, model=args.model)

    if args.json:
        print(json.dumps(comparison, indent=2))
    else:
        print_comparison_table(comparison)
    return 1 if "error" in comparison else 0


if __name__ == "__main__":
    sys.exit(main())
