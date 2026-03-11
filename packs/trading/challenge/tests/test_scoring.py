"""Tests for trading scoring placeholder."""

from __future__ import annotations

from starter_challenge.scoring import score_prediction


class TestScoringContract:
    """Shape/type requirements — must pass for ANY valid implementation."""

    def test_returns_dict(self):
        result = score_prediction({"action": "buy", "amount": 100}, {})
        assert isinstance(result, dict)

    def test_has_value_key(self):
        result = score_prediction({"action": "buy", "amount": 100}, {})
        assert "value" in result
        assert isinstance(result["value"], (int, float))

    def test_has_success_key(self):
        result = score_prediction({"action": "buy", "amount": 100}, {})
        assert "success" in result
        assert isinstance(result["success"], bool)

    def test_has_failed_reason_key(self):
        result = score_prediction({"action": "buy", "amount": 100}, {})
        assert "failed_reason" in result
        assert result["failed_reason"] is None
