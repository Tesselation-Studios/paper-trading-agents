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
