"""Tests for E2E verification scoring guards.

Issue #4: The 0.0 scoring stub can slip past E2E verification.
The all-identical check should be a failure, not just a warning.
"""

from __future__ import annotations


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
