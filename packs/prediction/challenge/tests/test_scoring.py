"""Tests for prediction scoring.

All tests use Pydantic models — the engine always coerces to typed objects.
"""

from __future__ import annotations

from starter_challenge.scoring import (
    PredictionGroundTruth,
    PredictionOutput,
    PredictionScoreResult,
    score_prediction,
)


class TestScoringContract:
    """Shape/type requirements — must pass for ANY valid implementation."""

    def test_returns_pydantic_model(self):
        result = score_prediction(
            PredictionOutput(value=0.5),
            PredictionGroundTruth(profit=0.01, entry_price=40000, resolved_price=40040),
        )
        assert isinstance(result, PredictionScoreResult)

    def test_has_value_field(self):
        result = score_prediction(
            PredictionOutput(value=0.5),
            PredictionGroundTruth(profit=0.01, entry_price=40000, resolved_price=40040),
        )
        assert isinstance(result.value, (int, float))

    def test_has_success_field(self):
        result = score_prediction(
            PredictionOutput(value=0.5),
            PredictionGroundTruth(profit=0.01, entry_price=40000, resolved_price=40040),
        )
        assert isinstance(result.success, bool)

    def test_has_failed_reason_field(self):
        result = score_prediction(
            PredictionOutput(value=0.5),
            PredictionGroundTruth(profit=0.01, entry_price=40000, resolved_price=40040),
        )
        assert result.failed_reason is None or isinstance(result.failed_reason, str)


class TestScoringBehavior:
    """Behavioral tests for directional prediction scoring."""

    def test_correct_bullish_scores_positive(self):
        """Bullish prediction + price up = positive score."""
        result = score_prediction(
            PredictionOutput(value=0.5),
            PredictionGroundTruth(profit=0.02, entry_price=40000, resolved_price=40080),
        )
        assert result.value > 0
        assert result.direction_correct is True

    def test_correct_bearish_scores_positive(self):
        """Bearish prediction + price down = positive score."""
        result = score_prediction(
            PredictionOutput(value=-0.5),
            PredictionGroundTruth(
                profit=-0.02, entry_price=40000, resolved_price=39920
            ),
        )
        assert result.value > 0
        assert result.direction_correct is True

    def test_wrong_direction_scores_negative(self):
        """Bullish prediction + price down = negative score."""
        result = score_prediction(
            PredictionOutput(value=0.5),
            PredictionGroundTruth(
                profit=-0.02, entry_price=40000, resolved_price=39920
            ),
        )
        assert result.value < 0
        assert result.direction_correct is False

    def test_zero_prediction_scores_zero(self):
        """No conviction = zero score."""
        result = score_prediction(
            PredictionOutput(value=0.0),
            PredictionGroundTruth(profit=0.02, entry_price=40000, resolved_price=40080),
        )
        assert result.value == 0.0

    def test_higher_conviction_amplifies_score(self):
        """Larger |prediction| = larger |score|."""
        gt = PredictionGroundTruth(profit=0.01, entry_price=40000, resolved_price=40040)
        low = score_prediction(PredictionOutput(value=0.2), gt)
        high = score_prediction(PredictionOutput(value=0.8), gt)
        assert abs(high.value) > abs(low.value)

    def test_zero_entry_price_fails_gracefully(self):
        """Zero entry price returns failure."""
        result = score_prediction(
            PredictionOutput(value=0.5),
            PredictionGroundTruth(profit=0.01, entry_price=0, resolved_price=100),
        )
        assert result.success is False
        assert result.failed_reason is not None

    def test_default_output_scores_zero(self):
        """Default InferenceOutput (value=0.0) scores 0.0."""
        result = score_prediction(
            PredictionOutput(),
            PredictionGroundTruth(profit=0.01, entry_price=40000, resolved_price=40040),
        )
        assert result.value == 0.0
        assert result.success is True
