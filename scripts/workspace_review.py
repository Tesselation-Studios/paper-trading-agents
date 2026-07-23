#!/usr/bin/env python3
"""
Workspace Review — mechanized consistency checks for strategy.md/
params.json/executor.py, so drift between them is caught automatically
instead of by accident (or not at all).

Ported from paper-trading-rebuild's validate_prompt_format.py +
check_risk_prompt_consistency.py + pre_market_gate.py pattern. Two real
incidents this week are exactly what this catches:
  1. 2026-07-22: strategy.md dropped the CHOPPY entry gate in a v1.3
     revert, but tick_prompt.md/the agent's mental model stayed stale for
     ~2 hours — a version mismatch this script now flags.
  2. 2026-07-23: params.json still carried v1.1/v1.2 param blocks
     (entry_rules, regime_sizing, trim, quality_gate, exit_rules,
     risk_guards.max_holding_days) that nothing in scripts/*.py ever
     read — found by hand via grep; this mechanizes that same process.

Findings are tiered:
  - critical: would actually break execution (invalid params.json,
    missing keys executor.py/tick_prompt.md depend on). Blocks trading
    when run with --gate.
  - warning: real drift, but not safety-critical (dead params, gate
    naming drift, version mismatch). Never blocks a live tick — matches
    this repo's "never let a tick stall" philosophy — but does fail CI
    under --strict, since a failed CI run is cheap and a blocked trading
    day is not.

check_dead_params() is a heuristic (substring match across scripts/*.py),
not real static analysis — a key referenced only via an f-string or a
dynamically-built dict access could produce a false positive. Same
honesty standard as replay_check.py's own documented caveats: useful
signal, not proof.

Usage:
    python3 scripts/workspace_review.py                # print findings, exit 1 if critical
    python3 scripts/workspace_review.py --strict        # exit 1 on warnings too (CI)
    python3 scripts/workspace_review.py --gate          # also write/clear state/.workspace_blocked
    python3 scripts/workspace_review.py --gate --clear  # manually clear the sentinel
    python3 scripts/workspace_review.py --gate --status # print current gate status
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PARAMS_PATH = REPO_ROOT / "params.json"
STRATEGY_PATH = REPO_ROOT / "strategy.md"
EXECUTOR_PATH = REPO_ROOT / "scripts" / "executor.py"
SCRIPTS_DIR = REPO_ROOT / "scripts"
STATE_DIR = REPO_ROOT / "state"
SENTINEL_PATH = STATE_DIR / ".workspace_blocked"

REQUIRED_TOP_LEVEL_KEYS = ["agent", "risk", "guardrail_gates", "universe", "watchlist"]

# Blocks worth checking for dead keys — numeric/behavioral params a script
# should be reading somewhere. Deliberately excludes purely descriptive
# blocks (journal, strategy_files, executor, not_yet_built) that aren't
# "rules" in the sense that mattered for the Jul 23 incident.
DEAD_PARAM_BLOCKS = ["risk", "risk_guards", "universe", "watchlist", "alpaca", "synthesis"]

Finding = Tuple[str, str]  # (severity, message)


def _load_params_raw() -> str:
    return PARAMS_PATH.read_text() if PARAMS_PATH.exists() else ""


def check_params_json_valid(raw: str) -> Tuple[List[Finding], Dict[str, Any]]:
    findings: List[Finding] = []
    if not PARAMS_PATH.exists():
        return [("critical", "params.json does not exist")], {}
    try:
        params = json.loads(raw)
    except json.JSONDecodeError as e:
        return [("critical", f"params.json is not valid JSON: {e}")], {}

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in params:
            findings.append(("critical", f"params.json is missing required top-level key '{key}'"))
    return findings, params


def check_strategy_md_structure(text: str) -> List[Finding]:
    findings: List[Finding] = []
    if not STRATEGY_PATH.exists() or not text.strip():
        return [("critical", "strategy.md is missing or empty")]
    if not re.search(r"^#\s+.*stonks\.strat:v[\d.]+", text, re.MULTILINE):
        findings.append(("critical", "strategy.md has no parseable version header (expected '# ... stonks.strat:vX.Y')"))
    if "## Version History" not in text:
        findings.append(("warning", "strategy.md has no '## Version History' section"))
    return findings


def check_version_sync(params: Dict[str, Any], strategy_text: str) -> List[Finding]:
    if not params or not strategy_text:
        return []  # already flagged by the structural checks above
    params_version = params.get("strategy_version", "")
    match = re.search(r"stonks\.strat:v[\d.]+", strategy_text)
    strategy_version = match.group(0) if match else None
    if strategy_version and params_version and strategy_version != params_version:
        return [("warning", f"version mismatch: params.json.strategy_version='{params_version}' "
                             f"but strategy.md header says '{strategy_version}'")]
    return []


def _extract_guardrail_gate_names(guardrail_gates: Dict[str, Any]) -> set:
    return {k for k in guardrail_gates if not k.startswith("_")}


def _extract_executor_gate_references(executor_text: str) -> set:
    """Every guardrail_gates.<name> this file actually consumes — both the
    GATES dict (order-time gates) and toggles.get("name") calls elsewhere
    (check_stops's hard_stop/trailing_stop/position_size_trim aren't in
    GATES at all, they're read directly).
    """
    gates_dict_keys = set(re.findall(r'^\s*"([a-z_]+)":\s*gate_\w+', executor_text, re.MULTILINE))
    toggle_keys = set(re.findall(r'toggles\.get\(\s*"([a-z_]+)"', executor_text))
    return gates_dict_keys | toggle_keys


def check_guardrail_gates_drift(params: Dict[str, Any], executor_text: str) -> List[Finding]:
    if not params or not executor_text:
        return []
    declared = _extract_guardrail_gate_names(params.get("guardrail_gates", {}))
    referenced = _extract_executor_gate_references(executor_text)

    findings: List[Finding] = []
    for name in sorted(declared - referenced):
        findings.append(("warning", f"guardrail_gates.{name} is declared in params.json but "
                                     f"executor.py never reads it — dead toggle"))
    for name in sorted(referenced - declared):
        findings.append(("warning", f"executor.py reads guardrail_gates.{name} but params.json doesn't "
                                     f"declare it (defaults to enabled) — undocumented gate"))
    return findings


def _live_docs_text() -> str:
    """Text of every doc the agent actually reads each tick/nightly cycle
    — many params.json values are interpreted by the agent's own judgment
    in prose (tick_prompt.md/strategy.md/skills), not by code. Excludes
    v4-spec.md/README*.md (design/aspirational docs, not live-consumed —
    including them would mask genuinely dead params behind future-roadmap
    mentions).
    """
    text = ""
    candidates = list(REPO_ROOT.glob("*.md")) + list((REPO_ROOT / "skills").glob("*.md")) + \
        list((REPO_ROOT / "strategies").glob("*.md"))
    for f in candidates:
        if f.name == "v4-spec.md" or f.name.upper().startswith("README"):
            continue
        text += f.read_text()
    return text


def check_dead_params(params: Dict[str, Any]) -> List[Finding]:
    if not params:
        return []
    scripts_text = ""
    for f in sorted(SCRIPTS_DIR.glob("*.py")):
        scripts_text += f.read_text()
    combined_text = scripts_text + _live_docs_text()

    findings: List[Finding] = []
    for block_name in DEAD_PARAM_BLOCKS:
        block = params.get(block_name)
        if not isinstance(block, dict):
            continue
        for key in block:
            if key.startswith("_"):
                continue
            if key not in combined_text:
                findings.append(("warning", f"params.json.{block_name}.{key} isn't referenced in "
                                             f"scripts/*.py or any live-consumed doc — possibly dead"))
    return findings


def run_all_checks() -> Dict[str, Any]:
    params_raw = _load_params_raw()
    strategy_text = STRATEGY_PATH.read_text() if STRATEGY_PATH.exists() else ""
    executor_text = EXECUTOR_PATH.read_text() if EXECUTOR_PATH.exists() else ""

    findings: List[Finding] = []
    params_findings, params = check_params_json_valid(params_raw)
    findings += params_findings
    findings += check_strategy_md_structure(strategy_text)
    findings += check_version_sync(params, strategy_text)
    findings += check_guardrail_gates_drift(params, executor_text)
    findings += check_dead_params(params)

    critical = [msg for sev, msg in findings if sev == "critical"]
    warnings = [msg for sev, msg in findings if sev == "warning"]
    return {"critical": critical, "warnings": warnings, "ok": not critical and not warnings}


def gate_status() -> str:
    if SENTINEL_PATH.exists():
        return f"BLOCKED: {SENTINEL_PATH.read_text().strip()}"
    return "CLEAR — trading allowed"


def clear_gate() -> None:
    if SENTINEL_PATH.exists():
        SENTINEL_PATH.unlink()


def block_gate(reason: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    SENTINEL_PATH.write_text(f"Blocked at {timestamp}: {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 on warnings too (for CI)")
    parser.add_argument("--gate", action="store_true", help="also manage state/.workspace_blocked sentinel")
    parser.add_argument("--clear", action="store_true", help="manually clear the gate sentinel (requires --gate)")
    parser.add_argument("--status", action="store_true", help="print gate status and exit (requires --gate)")
    args = parser.parse_args()

    if args.gate and args.status:
        print(gate_status())
        return 0

    if args.gate and args.clear:
        clear_gate()
        print("Gate cleared — trading allowed.")
        return 0

    report = run_all_checks()
    print(json.dumps(report, indent=2))

    if args.gate:
        if report["critical"]:
            block_gate("; ".join(report["critical"]))
        else:
            clear_gate()

    if report["critical"]:
        return 1
    if args.strict and report["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
