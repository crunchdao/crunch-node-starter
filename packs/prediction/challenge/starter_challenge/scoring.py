"""Prediction scoring: directional accuracy with magnitude scaling.

score = sign_match * |prediction|

Correct direction with higher conviction = higher score.
Wrong direction with higher conviction = larger penalty.

All inputs are Pydantic models — the engine coerces raw dicts before calling.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PredictionOutput(BaseModel):
    """Model output: directional prediction."""

    value: float = Field(default=0.0)


class PredictionGroundTruth(BaseModel):
    """Ground truth: realized price return."""

    model_config = ConfigDict(extra="allow")

    profit: float = 0.0
    entry_price: float = 0.0
    resolved_price: float = 0.0
    direction_up: bool = False


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
        prediction: Model output with ``value`` field (Pydantic model).
        ground_truth: Resolved outcome with ``profit`` field (Pydantic model).

    Returns:
        PredictionScoreResult with directional accuracy metrics.
    """
    actual_return = ground_truth.profit

    if ground_truth.entry_price == 0:
        return PredictionScoreResult(
            success=False,
            failed_reason="entry price is zero",
        )

    direction_correct = (prediction.value > 0) == (actual_return > 0)
    score = abs(prediction.value) if direction_correct else -abs(prediction.value)

    return PredictionScoreResult(
        value=score,
        actual_return=actual_return,
        direction_correct=direction_correct,
    )
