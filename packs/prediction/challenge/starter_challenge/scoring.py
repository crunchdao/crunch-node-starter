"""Prediction scoring: directional accuracy with magnitude scaling.

score = sign_match * |prediction| * |actual_return|

Correct direction with higher conviction = higher score.
Wrong direction with higher conviction = larger penalty.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PredictionOutput(BaseModel):
    """Model output: directional prediction."""

    value: float = Field(default=0.0)


class PredictionGroundTruth(BaseModel):
    """Ground truth: realized profit."""

    model_config = ConfigDict(extra="allow")
    profit: float = 0.0


class PredictionScoreResult(BaseModel):
    """Score output: directional accuracy."""

    model_config = ConfigDict(extra="allow")
    value: float = 0.0
    actual_return: float = 0.0
    direction_correct: bool = False
    success: bool = True
    failed_reason: str | None = None


def score_prediction(
    prediction: PredictionOutput, ground_truth: PredictionGroundTruth
) -> PredictionScoreResult:
    """Score a directional prediction against realized return.

    Args:
        prediction: Model output with ``value`` field.
        ground_truth: Resolved outcome with ``profit`` field.

    Returns:
        PredictionScoreResult with directional accuracy metrics.
    """
    actual_return = ground_truth.profit

    direction_correct = (prediction.value > 0 and actual_return > 0) or (
        prediction.value < 0 and actual_return < 0
    )

    # Score: prediction * actual_return (positive when directions match)
    value = prediction.value * actual_return

    return PredictionScoreResult(
        value=value,
        actual_return=actual_return,
        direction_correct=direction_correct,
    )
