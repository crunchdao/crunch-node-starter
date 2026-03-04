"""Tests that ScoreResult is visible and documented.

Issue #3: ScoreResult not shown in scaffold config — users have to
read crunch-node source to know it exists.
"""

from __future__ import annotations


class TestScoreResultVisible:
    """ScoreResult should be self-documenting."""

    def test_score_result_has_meaningful_docstring(self):
        from crunch_node.crunch_config import ScoreResult

        assert ScoreResult.__doc__ is not None
        assert len(ScoreResult.__doc__) > 30, (
            "ScoreResult docstring should explain purpose and customization"
        )
        assert (
            "customize" in ScoreResult.__doc__.lower()
            or "override" in ScoreResult.__doc__.lower()
            or "example" in ScoreResult.__doc__.lower()
        ), "ScoreResult docstring should mention how to customize it"

    def test_score_result_value_field_has_description(self):
        from crunch_node.crunch_config import ScoreResult

        field = ScoreResult.model_fields["value"]
        assert field.description is not None and len(field.description) > 10, (
            "ScoreResult.value should have a description"
        )

    def test_score_result_success_field_has_description(self):
        from crunch_node.crunch_config import ScoreResult

        field = ScoreResult.model_fields["success"]
        assert field.description is not None and len(field.description) > 10, (
            "ScoreResult.success should have a description"
        )
