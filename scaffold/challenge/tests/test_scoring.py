"""Tests for the scoring function.

The scaffold ships with a stub that returns 0.0. These tests serve two purposes:

1. **Contract tests** — assert the shape and types that the pipeline requires.
   These pass regardless of implementation.
2. **Stub detection** — assert that real scoring produces non-zero values for
   known inputs. These FAIL against the stub, signalling that scoring must be
   implemented before deploy. Mark them xfail until real scoring is written.
"""

from __future__ import annotations

import pytest
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
    """Behavioral expectations — these MUST fail against the 0.0 stub.

    When you implement real scoring, remove the xfail markers.
    If these pass with the stub, the test is wrong.
    """

    @pytest.mark.xfail(
        reason="Stub returns 0.0 — implement real scoring in scoring.py",
        strict=True,
    )
    def test_correct_prediction_scores_positive(self):
        """A prediction in the right direction should score > 0."""
        # Bullish prediction, price went up
        result = score_prediction(
            {"value": 0.5}, {"return": 0.02, "direction_up": True}
        )
        assert result["value"] > 0, (
            "Correct directional prediction should score positive"
        )

    @pytest.mark.xfail(
        reason="Stub returns 0.0 — implement real scoring in scoring.py",
        strict=True,
    )
    def test_wrong_prediction_scores_negative(self):
        """A prediction in the wrong direction should score < 0."""
        # Bullish prediction, price went down
        result = score_prediction(
            {"value": 0.5}, {"return": -0.02, "direction_up": False}
        )
        assert result["value"] < 0, "Wrong directional prediction should score negative"

    @pytest.mark.xfail(
        reason="Stub returns 0.0 — implement real scoring in scoring.py",
        strict=True,
    )
    def test_different_inputs_produce_different_scores(self):
        """Scoring must differentiate between predictions."""
        good = score_prediction({"value": 0.8}, {"return": 0.05, "direction_up": True})
        bad = score_prediction({"value": 0.8}, {"return": -0.05, "direction_up": False})
        assert good["value"] != bad["value"], (
            "Different ground truths should produce different scores"
        )
