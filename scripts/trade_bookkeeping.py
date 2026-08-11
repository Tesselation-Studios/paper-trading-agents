#!/usr/bin/env python3
"""Post-SELL bookkeeping — bankroll ceiling + Postgres outcome label +
decisions-table logging. Extracted 2026-08-11 from executor.py. Depends on
guardrail_gates.py (record_experience_outcome) -- extracted third, after
guardrail_gates.py, specifically so this dependency never requires a
circular import.
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import guardrail_gates

def close_trade_outcome(account: str, ticker: str, entry_price: float, exit_price: float, qty: int,
                         position_entry_time: Optional[str] = None) -> Dict[str, Any]:
    """Called once a SELL actually executes. Updates bankroll.py's
    win/loss-adaptive ceiling, experience.json's total_wins/total_losses/
    consecutive-streak counters (2026-07-27, see _load_experience's
    docstring), AND labels the Postgres training_examples outcome
    (trading.training_examples.label_win/label_return_pct) — this used to
    be two separate concerns, with the Postgres label depending on the LLM
    remembering a second manual `record_decision.py close` call per
    tick_prompt.md step 9. Confirmed 2026-07-22: roughly half of real
    closed trades that week never got labeled this way. Mechanized here
    instead, same trigger point as the bankroll update, which was already
    reliable.

    Fail-open on the Postgres side — a labeling failure shouldn't look like
    a trade failure, the order already executed by the time this runs.
    experience.json bookkeeping is likewise fail-open internally (see
    record_experience_outcome).

    Any one-off script correcting a historical realized_pnl (see
    scripts/backfill_*.py) must update bankroll_state AND experience.json
    together, not just positions.realized_pnl — see
    check_experience_bankroll_sync() in workspace_review.py, which flags
    the two drifting apart.

    2026-08-01: position_entry_time (positions.entry_time of the position
    being closed) is passed straight through to record_trade_close as the
    correlation key for WHICH training_examples row gets the label. Without
    it, labeling fell back to "the newest unlabeled row for this ticker",
    which on a SELL is the SELL's own row — so BUY rows carrying the actual
    predictive signals sat unlabeled forever and the scorecard learned
    nothing. See decisions.record_trade_close.
    """
    pnl = (exit_price - entry_price) * qty
    return_pct = (exit_price - entry_price) / entry_price * 100 if entry_price else 0.0
    # An exact $0.00 close is a near-impossible-by-chance signal that
    # exit_price silently defaulted to entry_price again (the same bug
    # shape as the 2026-08-03/2026-08-10 ZBRA/DXCM/OOMA/VSXY incidents,
    # see the SELL CLI path's own comments) -- flagged generally here
    # rather than only at the one already-known trigger site, since
    # close_trade_outcome() is the single choke point for every real
    # close regardless of caller or reason.
    zero_pnl_anomaly = (pnl == 0.0)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import bankroll
    state = bankroll.read_bankroll()
    bankroll.recalc_ceiling(state, pnl, is_win=(pnl > 0))
    bankroll.write_bankroll(state)
    guardrail_gates.record_experience_outcome(is_win=(pnl > 0))

    outcome_label_warning = None
    try:
        import decisions
        close_result = decisions.record_trade_close(
            trader_id=account, ticker=ticker, trade_id=None,
            pnl=pnl, return_pct=return_pct,
            position_entry_time=position_entry_time,
        )
        if "error" in close_result:
            outcome_label_warning = close_result["error"]
    except Exception as e:
        outcome_label_warning = f"could not label trade outcome: {e}"

    return {"pnl": pnl, "return_pct": return_pct, "outcome_label_warning": outcome_label_warning,
             "zero_pnl_anomaly": zero_pnl_anomaly}


def _record_decision_row(action: str, ticker: str, conviction, rationale: str,
                          features: Dict[str, Any], regime: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Mechanized `decisions` table write, called from the BUY/SELL paths
    below on every real fill. Previously the only writer of this table was
    the standalone `record_decision.py decision` CLI, a second manual step
    per tick_prompt.md step 9 distinct from the executor call itself —
    confirmed stopped firing reliably after 2026-07-31 (decisions table: 34
    rows, all before then, while real trades kept happening and
    experience.json's total_trades -- mechanized separately, see
    _load_experience's docstring -- kept incrementing normally). Same
    root-cause pattern as that fix and as close_trade_outcome's Postgres
    labeling above, applied to the one table neither of those touches.

    Fail-open, same philosophy as close_trade_outcome/record_entry_example
    above — a logging failure must never look like a failed order, the
    order has already executed by the time this runs.
    """
    try:
        import decisions
        result = decisions.record_decision(
            trader_id="stonks", ticker=ticker, action=action,
            rationale=rationale or "", conviction=conviction if conviction is not None else 0.0,
            regime=regime, features=features or {},
        )
        if result.get("error"):
            print(json.dumps({"warning": f"decision log failed: {result['error']}"}), file=sys.stderr)
        return result
    except Exception as e:
        print(json.dumps({"warning": f"decision log failed: {e}"}), file=sys.stderr)
        return None
