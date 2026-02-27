"""Tests for prediction scoring."""

from __future__ import annotations

from starter_challenge.scoring import score_prediction


class TestScoringContract:
    """Shape/type requirements — must pass for ANY valid implementation."""

    def test_returns_dict(self):
        result = score_prediction({"value": 0.5}, {"return": 0.01})
        assert isinstance(result, dict)

    def test_has_value_key(self):
        result = score_prediction({"value": 0.5}, {"return": 0.01})
        assert "value" in result
        assert isinstance(result["value"], (int, float))

    def test_has_success_key(self):
        result = score_prediction({"value": 0.5}, {"return": 0.01})
        assert "success" in result
        assert isinstance(result["success"], bool)

    def test_has_failed_reason_key(self):
        result = score_prediction({"value": 0.5}, {"return": 0.01})
        assert "failed_reason" in result
        assert result["failed_reason"] is None or isinstance(
            result["failed_reason"], str
        )


class TestScoringBehavior:
    """Behavioral tests for directional prediction scoring."""

    def test_correct_bullish_scores_positive(self):
        """Bullish prediction + price up = positive score."""
        result = score_prediction({"value": 0.5}, {"return": 0.02})
        assert result["value"] > 0
        assert result["direction_correct"] is True

    def test_correct_bearish_scores_positive(self):
        """Bearish prediction + price down = positive score."""
        result = score_prediction({"value": -0.5}, {"return": -0.02})
        assert result["value"] > 0
        assert result["direction_correct"] is True

    def test_wrong_direction_scores_negative(self):
        """Bullish prediction + price down = negative score."""
        result = score_prediction({"value": 0.5}, {"return": -0.02})
        assert result["value"] < 0
        assert result["direction_correct"] is False

    def test_zero_prediction_scores_zero(self):
        """No conviction = zero score."""
        result = score_prediction({"value": 0.0}, {"return": 0.02})
        assert result["value"] == 0.0

    def test_higher_conviction_amplifies_score(self):
        """Larger |prediction| = larger |score|."""
        low = score_prediction({"value": 0.2}, {"return": 0.01})
        high = score_prediction({"value": 0.8}, {"return": 0.01})
        assert abs(high["value"]) > abs(low["value"])

    def test_invalid_prediction_fails_gracefully(self):
        """Non-numeric prediction returns failure."""
        result = score_prediction({"value": "bad"}, {"return": 0.01})
        assert result["success"] is False
        assert result["failed_reason"] is not None

    def test_missing_value_defaults_to_zero(self):
        """Missing value key treated as 0.0."""
        result = score_prediction({}, {"return": 0.01})
        assert result["value"] == 0.0
        assert result["success"] is True
