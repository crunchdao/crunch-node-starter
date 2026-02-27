"""Tests for tournament scoring."""

from __future__ import annotations

from starter_challenge.scoring import score_prediction


class TestScoringContract:
    """Shape/type requirements — must pass for ANY valid implementation."""

    def test_returns_dict(self):
        result = score_prediction({"prediction": 0.5}, {"target": 0.3})
        assert isinstance(result, dict)

    def test_has_value_key(self):
        result = score_prediction({"prediction": 0.5}, {"target": 0.3})
        assert "value" in result
        assert isinstance(result["value"], (int, float))

    def test_has_success_key(self):
        result = score_prediction({"prediction": 0.5}, {"target": 0.3})
        assert "success" in result
        assert isinstance(result["success"], bool)

    def test_has_failed_reason_key(self):
        result = score_prediction({"prediction": 0.5}, {"target": 0.3})
        assert "failed_reason" in result
        assert result["failed_reason"] is None or isinstance(
            result["failed_reason"], str
        )


class TestScoringBehavior:
    """Behavioral tests for residual-based tournament scoring."""

    def test_perfect_prediction_scores_zero(self):
        """Exact match = zero residual = max score (0.0)."""
        result = score_prediction({"prediction": 0.5}, {"target": 0.5})
        assert result["value"] == 0.0
        assert result["residual"] == 0.0

    def test_closer_prediction_scores_higher(self):
        """Closer to target = less negative = higher score."""
        close = score_prediction({"prediction": 0.51}, {"target": 0.5})
        far = score_prediction({"prediction": 1.0}, {"target": 0.5})
        assert close["value"] > far["value"]

    def test_score_is_always_non_positive(self):
        """Negative squared residual — score is always <= 0."""
        result = score_prediction({"prediction": 5.0}, {"target": -5.0})
        assert result["value"] <= 0.0

    def test_symmetric_errors(self):
        """Over-prediction and under-prediction by same amount = same score."""
        over = score_prediction({"prediction": 0.7}, {"target": 0.5})
        under = score_prediction({"prediction": 0.3}, {"target": 0.5})
        assert abs(over["value"] - under["value"]) < 1e-9

    def test_residual_tracked(self):
        """Score result includes the raw residual."""
        result = score_prediction({"prediction": 0.8}, {"target": 0.5})
        assert abs(result["residual"] - 0.3) < 1e-9

    def test_invalid_prediction_fails_gracefully(self):
        """Non-numeric prediction returns failure."""
        result = score_prediction({"prediction": "bad"}, {"target": 0.5})
        assert result["success"] is False
        assert result["failed_reason"] is not None

    def test_missing_prediction_defaults_to_zero(self):
        """Missing prediction key treated as 0.0."""
        result = score_prediction({}, {"target": 0.5})
        assert result["success"] is True
        assert result["prediction"] == 0.0
