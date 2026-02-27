"""Tournament scoring: per-prediction residual-based score.

Individual predictions are scored by residual (prediction - target).
The primary ranking metric is IC (computed at the aggregation level
from collections of these per-prediction scores).
"""

from __future__ import annotations


def score_prediction(prediction: dict, ground_truth: dict) -> dict:
    """Score a single tournament prediction against ground truth.

    Args:
        prediction: Model output, expects ``{"prediction": float}``.
        ground_truth: Resolved outcome, expects ``{"target": float}``.

    Returns:
        Dict matching ScoreResult shape.
    """
    try:
        pred_value = float(prediction.get("prediction", 0.0))
    except (TypeError, ValueError):
        return {
            "value": 0.0,
            "prediction": 0.0,
            "target": 0.0,
            "residual": 0.0,
            "success": False,
            "failed_reason": f"Invalid prediction: {prediction.get('prediction')!r}",
        }

    target = float(ground_truth.get("target", 0.0))
    residual = pred_value - target

    # Score: negative squared residual (closer to target = higher score)
    value = -(residual**2)

    return {
        "value": value,
        "prediction": pred_value,
        "target": target,
        "residual": residual,
        "success": True,
        "failed_reason": None,
    }
