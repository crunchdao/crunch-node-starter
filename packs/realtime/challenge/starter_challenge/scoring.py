"""Prediction scoring: predict the next return.

Models output a signed prediction of the next-minute return.
Score = prediction × realized_return (linear scoring rule).

This is a **proper scoring rule** — the optimal strategy is to output
your honest expected return estimate. No gaming, no magnitude tricks.

- Positive score: you predicted the right direction
- Negative score: you predicted the wrong direction
- Magnitude matters: bigger bets on correct moves score higher

All inputs are Pydantic models — the engine coerces raw dicts before calling.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PredictionOutput(BaseModel):
    """Model output: predicted return (signed).

    Positive = expect price to go up.
    Negative = expect price to go down.
    Magnitude = conviction (unconstrained, but [-1, 1] is typical).
    """

    value: float = Field(default=0.0)


class PredictionGroundTruth(BaseModel):
    """Ground truth: realized price return over the resolution horizon."""

    model_config = ConfigDict(extra="allow")

    profit: float = 0.0
    entry_price: float = 0.0
    resolved_price: float = 0.0


class PredictionScoreResult(BaseModel):
    """Score output: prediction × realized return."""

    model_config = ConfigDict(extra="allow")

    value: float = 0.0
    prediction: float = 0.0
    actual_return: float = 0.0
    success: bool = True
    failed_reason: str | None = None


SCORE_SCALE: int = 10_000  # express score in basis-point units


def score_prediction(
    prediction: PredictionOutput, ground_truth: PredictionGroundTruth
) -> PredictionScoreResult:
    """Score = prediction × realized_return × 10,000.

    Linear scoring rule — proper, incentive-compatible, impossible to game.
    The optimal strategy is to output E[return | data].

    The 10,000× multiplier converts the raw product (≈10⁻⁸) into
    human-readable units without changing the incentive structure.
    """
    actual_return = ground_truth.profit

    if ground_truth.entry_price == 0:
        return PredictionScoreResult(
            success=False,
            failed_reason="entry price is zero",
        )

    score = prediction.value * actual_return * SCORE_SCALE

    return PredictionScoreResult(
        value=score,
        prediction=prediction.value,
        actual_return=actual_return,
    )
