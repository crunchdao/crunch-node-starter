"""Tests for E2E verification scoring guards.

Issue #4: The 0.0 scoring stub can slip past E2E verification.
The all-identical check should be a failure, not just a warning.
Also, the scoring stub itself should be detectable at startup.
"""

from __future__ import annotations


class TestScoringStubDetection:
    """Detect stub scoring functions that always return a constant."""

    def test_detects_constant_zero_scoring_stub(self):
        """A scoring function that returns 0.0 for varied inputs is a stub."""
        from coordinator_node.services.score import ScoreService

        def stub_scorer(prediction, ground_truth):
            return {"value": 0.0, "success": True, "failed_reason": None}

        is_stub, reason = ScoreService.detect_scoring_stub(stub_scorer)
        assert is_stub is True
        assert (
            "constant" in reason.lower()
            or "stub" in reason.lower()
            or "identical" in reason.lower()
        )

    def test_accepts_real_scoring_function(self):
        """A scoring function that produces varied outputs is not a stub."""
        from coordinator_node.services.score import ScoreService

        def real_scorer(prediction, ground_truth):
            pred_val = float(prediction.get("value", 0.0))
            actual_return = float(ground_truth.get("return", 0.0))
            # Returns different scores based on alignment of prediction vs actual
            score = pred_val * actual_return
            return {"value": score, "success": True}

        is_stub, reason = ScoreService.detect_scoring_stub(real_scorer)
        assert is_stub is False

    def test_detects_constant_nonzero_stub(self):
        """A function that always returns the same nonzero value is also a stub."""
        from coordinator_node.services.score import ScoreService

        def constant_scorer(prediction, ground_truth):
            return {"value": 1.0, "success": True}

        is_stub, reason = ScoreService.detect_scoring_stub(constant_scorer)
        assert is_stub is True


class TestVerifyE2EAllIdenticalScores:
    """verify_e2e should FAIL (not warn) on all-identical scores."""

    def test_all_identical_nonzero_scores_is_fatal(self):
        """If all scored predictions have the same value, it's a stub."""
        scored = [
            {"score_value": 1.0, "score_failed": False},
            {"score_value": 1.0, "score_failed": False},
            {"score_value": 1.0, "score_failed": False},
        ]

        from scaffold.node.scripts.verify_e2e import check_score_quality

        passed, reason = check_score_quality(scored)
        assert passed is False
        assert "identical" in reason.lower()

    def test_varied_scores_pass(self):
        """Varied scores are fine."""
        scored = [
            {"score_value": 1.0, "score_failed": False},
            {"score_value": -1.0, "score_failed": False},
            {"score_value": 0.5, "score_failed": False},
        ]

        from scaffold.node.scripts.verify_e2e import check_score_quality

        passed, reason = check_score_quality(scored)
        assert passed is True

    def test_all_zero_scores_is_fatal(self):
        """All-zero scores should fail (already tested, but formalizing)."""
        scored = [
            {"score_value": 0.0, "score_failed": False},
            {"score_value": 0.0, "score_failed": False},
        ]

        from scaffold.node.scripts.verify_e2e import check_score_quality

        passed, reason = check_score_quality(scored)
        assert passed is False
