"""Prediction scoring: directional accuracy with magnitude scaling.

score = sign_match * |prediction| * |actual_return|

Correct direction with higher conviction = higher score.
Wrong direction with higher conviction = larger penalty.
"""

from __future__ import annotations


def score_prediction(prediction: dict, ground_truth: dict) -> dict:
    """Score a directional prediction against realized return.

    Args:
        prediction: Model output, expects ``{"value": float}``.
        ground_truth: Resolved outcome, expects ``{"profit": float}``.

    Returns:
        Dict matching ScoreResult shape.
    """
    try:
        pred_value = float(prediction.get("value", 0.0))
    except (TypeError, ValueError):
        return {
            "value": 0.0,
            "actual_return": 0.0,
            "direction_correct": False,
            "success": False,
            "failed_reason": f"Invalid prediction: {prediction.get('value')!r}",
        }

    actual_return = float(ground_truth.get("profit", 0.0))

    # Direction match
    direction_correct = (pred_value > 0 and actual_return > 0) or (
        pred_value < 0 and actual_return < 0
    )

    # Score: prediction * actual_return (positive when directions match)
    value = pred_value * actual_return

    return {
        "value": value,
        "actual_return": actual_return,
        "direction_correct": direction_correct,
        "success": True,
        "failed_reason": None,
    }
