"""Per-signal confidence scoring + reconciliation.

Defines the shape a decision's `features` should have when multiple signal
sources (sentiment/news, technical, regime, ...) each carry their own
direction + confidence, instead of being pre-blended into one conviction
number. This is the schema that gets stored in
trading.training_examples.features — deliberately just a convention, not
enforced by the DB (JSONB stays flexible on purpose), but validated here so
mistakes get caught early rather than silently stored as junk.

reconcile_signals() is a simple, fixed-weight combiner (weight = each
signal's own stated confidence) — the natural predecessor to the design
doc's "learned signal_weights" ML step. Get the schema and a sane baseline
combiner right first; learn better weights later once training_examples has
enough real (signal, outcome) rows to justify it.

That "later" arrived in a small way 2026-07-24: scripts/signal_scorecard.py
computes each signal's real empirical hit rate from trading.training_examples
once it has enough labeled rows (state/signal_scorecard.json). reconcile_signals()
takes that scorecard as an optional argument and nudges weight up/down from
the fixed baseline for signals with enough history — signals still below the
sample-size threshold are untouched, so this degrades to the original
fixed-weight behavior whenever there isn't real data to justify anything else.
"""

DIRECTIONS = {"bullish", "bearish", "neutral"}
_DIRECTION_SIGN = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}

# hit_rate 0.5 (chance) -> multiplier 1.0 (no change from baseline).
# Clamped so a short bad/good streak can't zero out or blow up a signal's weight.
_SCORECARD_MULT_MIN = 0.4
_SCORECARD_MULT_MAX = 1.8


def _scorecard_multiplier(name, scorecard):
    """1.0 if no scorecard, signal missing, or still insufficient_data."""
    if not scorecard:
        return 1.0
    entry = scorecard.get(name)
    if not entry or entry.get("status") != "scored":
        return 1.0
    hit_rate = entry.get("hit_rate")
    if hit_rate is None:
        return 1.0
    mult = 2.0 * hit_rate
    return max(_SCORECARD_MULT_MIN, min(_SCORECARD_MULT_MAX, mult))


def validate_signal_features(features):
    """Return a list of warnings (empty = clean). Never raises — this is a
    soft check, not a hard gate, so `features` stays flexible for
    exploration. Only checks entries shaped like {direction, confidence};
    other keys are left alone."""
    warnings = []
    if not isinstance(features, dict):
        return [f"features must be a dict, got {type(features).__name__}"]

    for name, val in features.items():
        if not isinstance(val, dict):
            continue  # not a signal-shaped entry, ignore
        if "direction" in val or "confidence" in val:
            direction = val.get("direction")
            confidence = val.get("confidence")
            if direction is not None and direction not in DIRECTIONS:
                warnings.append(f"{name}.direction={direction!r} not one of {sorted(DIRECTIONS)}")
            if confidence is not None and not (isinstance(confidence, (int, float)) and 0.0 <= confidence <= 1.0):
                warnings.append(f"{name}.confidence={confidence!r} must be a number in [0, 1]")
    return warnings


def reconcile_signals(features, scorecard=None):
    """Combine per-signal (direction, confidence) entries into one weighted
    recommendation. Ignores keys that aren't shaped like a signal (e.g. a
    'note' string) so `features` can still carry free-form context
    alongside scored signals.

    `scorecard` is the optional `signals` dict from state/signal_scorecard.json
    (load and pass it in; this module doesn't read files itself). Signals
    without enough labeled history in the scorecard use their raw
    self-reported confidence unchanged."""
    signal_entries = {
        name: val for name, val in (features or {}).items()
        if isinstance(val, dict) and "direction" in val and "confidence" in val
    }

    if not signal_entries:
        return {"recommendation": "neutral", "combined_confidence": 0.0,
                "agreement": True, "signal_count": 0, "detail": {}}

    weighted_sum = 0.0
    weight_total = 0.0
    directions_seen = set()
    detail = {}

    for name, val in signal_entries.items():
        direction = val["direction"]
        confidence = float(val["confidence"])
        mult = _scorecard_multiplier(name, scorecard)
        effective_weight = confidence * mult
        sign = _DIRECTION_SIGN.get(direction, 0.0)
        weighted_sum += sign * effective_weight
        weight_total += effective_weight
        if direction != "neutral":
            directions_seen.add(direction)
        detail[name] = {"direction": direction, "confidence": confidence,
                         "scorecard_multiplier": mult}

    lean = weighted_sum / weight_total if weight_total > 0 else 0.0
    agreement = len(directions_seen) <= 1

    if lean > 0.15:
        recommendation = "bullish"
    elif lean < -0.15:
        recommendation = "bearish"
    else:
        recommendation = "conflicted" if not agreement else "neutral"

    # Disagreement should erode confidence, not just average it away —
    # a 0.8-bullish vs 0.8-bearish split is NOT the same as mild 0.4 conviction,
    # it's a real conflict and should read as low-confidence, not neutral-confidence.
    avg_confidence = weight_total / len(signal_entries)
    combined_confidence = round(abs(lean) * avg_confidence if agreement else abs(lean) * avg_confidence * 0.5, 4)

    return {
        "recommendation": recommendation,
        "combined_confidence": combined_confidence,
        "agreement": agreement,
        "signal_count": len(signal_entries),
        "detail": detail,
    }
