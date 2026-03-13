"""Tournament scoring: MAPE-based house price prediction scoring.

Score = 1 - |prediction - actual| / actual, clamped to [0, 1].
Lower percentage error = higher score. A perfect prediction scores 1.0.

All inputs are Pydantic models — the engine coerces raw dicts before calling.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InferenceOutput(BaseModel):
    """Model output: predicted house price in dollars."""

    model_config = ConfigDict(extra="allow")

    prediction: float = Field(default=0.0)


class GroundTruth(BaseModel):
    """Ground truth: actual sale price."""

    model_config = ConfigDict(extra="allow")

    price: float = Field(default=0.0)


class ScoreResult(BaseModel):
    """Per-prediction score output."""

    model_config = ConfigDict(extra="allow")

    value: float = 0.0
    prediction: float = 0.0
    actual_price: float = 0.0
    pct_error: float = 0.0
    success: bool = True
    failed_reason: str | None = None


def score_prediction(
    prediction: InferenceOutput,
    ground_truth: GroundTruth,
) -> ScoreResult:
    """Score = 1 - |prediction - actual| / actual, clamped to [0, 1]."""
    if ground_truth.price <= 0:
        return ScoreResult(
            success=False,
            failed_reason="actual price is zero or negative",
        )

    pct_error = abs(prediction.prediction - ground_truth.price) / ground_truth.price
    value = max(0.0, 1.0 - pct_error)

    return ScoreResult(
        value=value,
        prediction=prediction.prediction,
        actual_price=ground_truth.price,
        pct_error=pct_error,
    )
