#!/usr/bin/env python3
"""Suggested share count for a given conviction tier -- a deterministic
helper, not a new gate. tick_prompt.md's sizing guidance is explicitly "an
anchor for judgment, not a formula" (step 8/9) -- this tool computes what
that anchor actually implies in shares, so choosing --qty doesn't require
mental math from a % target. All existing gates (gate_position_size,
gate_conviction_play, gate_long_play, ...) still run unconditionally on
whatever --qty is actually submitted; this changes nothing about them.

Added 2026-08-11 after a review found real trades were landing at 1 share
almost universally regardless of price/conviction/tier -- not a sizing-
formula bug (there wasn't one to have: --qty was always a bare CLI arg with
no computation behind it), but a design gap: probe was defined in absolute
shares (price-independent, see params.json risk._probe_source) and higher
tiers' %-based guidance in decision_heuristics.md was never mechanically
translated into a --qty number, so it got free-handed and defaulted low.

Usage:
    python3 scripts/position_sizing.py --tier conviction --price 301.56
    python3 scripts/position_sizing.py --tier probe --price 1.54 --portfolio-value 10420
"""
import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from executor import get_account, load_params  # noqa: E402

TIERS = ("probe", "standard", "long", "conviction")


def _target_pct_and_ceiling(tier: str, risk: dict) -> tuple[float, float | None]:
    """Returns (target_pct, dollar_ceiling_or_None) for a tier."""
    if tier == "probe":
        return float(risk.get("probe_position_pct", 1.5)), float(risk.get("probe_max_dollars", 150.0))
    if tier == "standard":
        return float(risk.get("max_position_pct", 6.0)), None
    if tier == "long":
        return float(risk.get("long_play", {}).get("position_size_pct", 3.0)), None
    if tier == "conviction":
        return float(risk.get("conviction_play", {}).get("position_size_pct", 20.0)), None
    raise ValueError(f"unknown tier {tier!r}, expected one of {TIERS}")


def suggest_shares(tier: str, price: float, portfolio_value: float, risk: dict = None) -> dict:
    """Suggest a share count targeting a tier's sizing guidance.

    Returns {tier, shares, target_pct, dollar_value, pct_of_portfolio}.
    `shares` is floored to a whole share, minimum 1 (this is a suggestion
    for --qty, which is always a whole-share int)."""
    if price <= 0 or portfolio_value <= 0:
        raise ValueError(f"price ({price}) and portfolio_value ({portfolio_value}) must both be positive")

    risk = risk if risk is not None else load_params().get("risk", {})
    target_pct, dollar_ceiling = _target_pct_and_ceiling(tier, risk)

    target_dollars = target_pct / 100.0 * portfolio_value
    if dollar_ceiling is not None:
        target_dollars = min(target_dollars, dollar_ceiling)

    shares = max(1, int(target_dollars // price))
    dollar_value = shares * price

    return {
        "tier": tier,
        "shares": shares,
        "target_pct": target_pct,
        "dollar_value": round(dollar_value, 2),
        "pct_of_portfolio": round(dollar_value / portfolio_value * 100, 2),
    }


STATE_DIR = SCRIPT_DIR.parent / "state"
SIGNAL_SCORECARD_PATH = STATE_DIR / "signal_scorecard.json"
TREE_SCORECARD_PATH = STATE_DIR / "tree_scorecard.json"
DEFAULT_GRADUATION_HIT_RATE_BAR = 0.70  # same bar as decision_tree.tier_promotion.hit_rate_bar_high_conviction


def graduation_readiness(hit_rate_bar: float = None, signal_path: Path = None,
                          tree_path: Path = None) -> dict:
    """Whether there's real evidence yet to lean on conviction/long-play
    sizing for a qualifying trade, rather than defaulting to probe by
    inertia -- the gap a 2026-08-11 review found (see this module's
    docstring): trades were landing at 1 share almost universally
    regardless of tier, and the tiers/gates existed but were simply never
    invoked. `--tier conviction/long` doesn't require this to be true (a
    real researched thesis is its own eligibility bar per
    decision_heuristics.md's conviction_play_anchor_v1) -- this is a
    second, independent signal: are the *signals themselves* (not just
    the thesis) proven enough yet to trust more.

    Reads state/signal_scorecard.json and state/tree_scorecard.json
    (both optional -- neither existing yet is not an error, just means no
    evidence accumulated). "Ready" means at least one signal or tree node
    has status "scored" (cleared its min-sample threshold) with a hit_rate
    at or above hit_rate_bar (default: the same 0.70 bar
    decision_tree.tier_promotion.hit_rate_bar_high_conviction already
    uses for tree-node tier promotion, reused here for a parallel
    "trust this evidence" question rather than inventing a second number).

    Note a "scored" signal is not automatically good evidence -- e.g. as
    of 2026-08-12, `technical` has n=23 (real sample size) but hit_rate
    0.3043, well under the bar. Clearing min_samples and clearing the
    hit_rate bar are two different things; only both together count."""
    if hit_rate_bar is None:
        hit_rate_bar = DEFAULT_GRADUATION_HIT_RATE_BAR
    signal_path = signal_path if signal_path is not None else SIGNAL_SCORECARD_PATH
    tree_path = tree_path if tree_path is not None else TREE_SCORECARD_PATH

    def _qualifying_entries(path: Path, key: str) -> dict:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        entries = data.get(key, {})
        return {
            name: entry for name, entry in entries.items()
            if entry.get("status") == "scored" and entry.get("hit_rate", 0.0) >= hit_rate_bar
        }

    qualifying_signals = _qualifying_entries(signal_path, "signals")
    qualifying_nodes = _qualifying_entries(tree_path, "nodes")

    return {
        "ready": bool(qualifying_signals or qualifying_nodes),
        "hit_rate_bar": hit_rate_bar,
        "qualifying_signals": qualifying_signals,
        "qualifying_tree_nodes": qualifying_nodes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier", required=True, choices=TIERS)
    parser.add_argument("--price", type=float, required=True)
    parser.add_argument("--portfolio-value", type=float, default=None,
                         help="Defaults to live account equity (Alpaca) if omitted")
    parser.add_argument("--account", default="stonks", choices=["stonks"])
    args = parser.parse_args()

    portfolio_value = args.portfolio_value
    if portfolio_value is None:
        account_data = get_account(args.account)
        portfolio_value = float(account_data.get("equity", 0))
        if portfolio_value <= 0:
            print("ERROR: could not fetch live portfolio value from Alpaca "
                  "and no --portfolio-value given", file=sys.stderr)
            return 1

    try:
        result = suggest_shares(args.tier, args.price, portfolio_value)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
