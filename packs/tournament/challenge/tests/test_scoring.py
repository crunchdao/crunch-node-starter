"""Tests for tournament scoring."""

from __future__ import annotations

from starter_challenge.scoring import (
    GroundTruth,
    InferenceOutput,
    ScoreResult,
    score_prediction,
)


class TestScoringContract:
    """Shape/type requirements — must pass for ANY valid implementation."""

    def test_returns_score_result(self):
        result = score_prediction(
            InferenceOutput(prediction=300_000), GroundTruth(price=350_000)
        )
        assert isinstance(result, ScoreResult)

    def test_has_value(self):
        result = score_prediction(
            InferenceOutput(prediction=300_000), GroundTruth(price=350_000)
        )
        assert isinstance(result.value, float)

    def test_has_success(self):
        result = score_prediction(
            InferenceOutput(prediction=300_000), GroundTruth(price=350_000)
        )
        assert isinstance(result.success, bool)

    def test_has_failed_reason(self):
        result = score_prediction(
            InferenceOutput(prediction=300_000), GroundTruth(price=350_000)
        )
        assert result.failed_reason is None or isinstance(result.failed_reason, str)


class TestScoringBehavior:
    def test_perfect_prediction_scores_one(self):
        result = score_prediction(
            InferenceOutput(prediction=350_000), GroundTruth(price=350_000)
        )
        assert result.value == 1.0
        assert result.pct_error == 0.0

    def test_closer_prediction_scores_higher(self):
        close = score_prediction(
            InferenceOutput(prediction=340_000), GroundTruth(price=350_000)
        )
        far = score_prediction(
            InferenceOutput(prediction=200_000), GroundTruth(price=350_000)
        )
        assert close.value > far.value

    def test_score_clamped_to_zero(self):
        """Prediction wildly off still scores >= 0."""
        result = score_prediction(
            InferenceOutput(prediction=1_000_000), GroundTruth(price=100_000)
        )
        assert result.value == 0.0

    def test_symmetric_errors(self):
        """10% over and 10% under produce the same score."""
        over = score_prediction(
            InferenceOutput(prediction=385_000), GroundTruth(price=350_000)
        )
        under = score_prediction(
            InferenceOutput(prediction=315_000), GroundTruth(price=350_000)
        )
        assert abs(over.value - under.value) < 1e-9

    def test_pct_error_tracked(self):
        result = score_prediction(
            InferenceOutput(prediction=280_000), GroundTruth(price=350_000)
        )
        assert abs(result.pct_error - 0.2) < 1e-9

    def test_zero_price_fails(self):
        result = score_prediction(
            InferenceOutput(prediction=300_000), GroundTruth(price=0)
        )
        assert result.success is False
        assert result.failed_reason is not None

    def test_defaults_to_zero_prediction(self):
        result = score_prediction(InferenceOutput(), GroundTruth(price=350_000))
        assert result.success is True
        assert result.prediction == 0.0
