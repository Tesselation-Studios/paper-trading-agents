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
"""

DIRECTIONS = {"bullish", "bearish", "neutral"}
_DIRECTION_SIGN = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}


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


def reconcile_signals(features):
    """Combine per-signal (direction, confidence) entries into one weighted
    recommendation. Ignores keys that aren't shaped like a signal (e.g. a
    'note' string) so `features` can still carry free-form context
    alongside scored signals."""
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
        sign = _DIRECTION_SIGN.get(direction, 0.0)
        weighted_sum += sign * confidence
        weight_total += confidence
        if direction != "neutral":
            directions_seen.add(direction)
        detail[name] = {"direction": direction, "confidence": confidence}

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
