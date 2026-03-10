"""Tests for prediction scoring (linear scoring rule).

score = prediction × actual_return × 10,000

All tests use Pydantic models — the engine always coerces to typed objects.
"""

from __future__ import annotations

from starter_challenge.scoring import (
    SCORE_SCALE,
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


class TestLinearScoring:
    """Behavioral tests for prediction × return scoring rule."""

    def test_correct_bullish(self):
        """Positive prediction × positive return = positive score."""
        result = score_prediction(
            PredictionOutput(value=0.5),
            PredictionGroundTruth(profit=0.02, entry_price=40000, resolved_price=40080),
        )
        assert result.value == 0.5 * 0.02 * SCORE_SCALE
        assert result.prediction == 0.5
        assert result.actual_return == 0.02

    def test_correct_bearish(self):
        """Negative prediction × negative return = positive score."""
        result = score_prediction(
            PredictionOutput(value=-0.5),
            PredictionGroundTruth(
                profit=-0.02, entry_price=40000, resolved_price=39920
            ),
        )
        assert result.value == (-0.5) * (-0.02) * SCORE_SCALE
        assert result.value > 0

    def test_wrong_direction(self):
        """Positive prediction × negative return = negative score."""
        result = score_prediction(
            PredictionOutput(value=0.5),
            PredictionGroundTruth(
                profit=-0.02, entry_price=40000, resolved_price=39920
            ),
        )
        assert result.value == 0.5 * (-0.02) * SCORE_SCALE
        assert result.value < 0

    def test_zero_prediction(self):
        """Zero prediction always scores zero."""
        result = score_prediction(
            PredictionOutput(value=0.0),
            PredictionGroundTruth(profit=0.02, entry_price=40000, resolved_price=40080),
        )
        assert result.value == 0.0

    def test_magnitude_matters(self):
        """Larger prediction × same return = larger score."""
        gt = PredictionGroundTruth(profit=0.01, entry_price=40000, resolved_price=40040)
        small = score_prediction(PredictionOutput(value=0.1), gt)
        large = score_prediction(PredictionOutput(value=1.0), gt)
        assert abs(large.value) > abs(small.value)

    def test_zero_entry_price_fails(self):
        """Zero entry price returns failure."""
        result = score_prediction(
            PredictionOutput(value=0.5),
            PredictionGroundTruth(profit=0.01, entry_price=0, resolved_price=100),
        )
        assert result.success is False
        assert result.failed_reason is not None

    def test_default_output_scores_zero(self):
        """Default PredictionOutput (value=0.0) scores 0.0."""
        result = score_prediction(
            PredictionOutput(),
            PredictionGroundTruth(profit=0.01, entry_price=40000, resolved_price=40040),
        )
        assert result.value == 0.0
        assert result.success is True

    def test_symmetry(self):
        """Flipping both prediction and return gives same score."""
        gt_up = PredictionGroundTruth(
            profit=0.01, entry_price=40000, resolved_price=40040
        )
        gt_down = PredictionGroundTruth(
            profit=-0.01, entry_price=40000, resolved_price=39960
        )
        score_bull = score_prediction(PredictionOutput(value=0.5), gt_up)
        score_bear = score_prediction(PredictionOutput(value=-0.5), gt_down)
        assert abs(score_bull.value - score_bear.value) < 1e-12
