"""Tests that InferenceOutput is visible and documented in the scaffold.

Issue #2: InferenceOutput not shown in scaffold config — users have to
read coordinator-node source to know it exists.
"""

from __future__ import annotations

from pathlib import Path

SCAFFOLD_CONFIG = (
    Path(__file__).resolve().parent.parent
    / "scaffold"
    / "node"
    / "config"
    / "crunch_config.py"
)


class TestInferenceOutputVisible:
    """Users should see InferenceOutput in the scaffold without reading engine source."""

    def test_inference_output_has_docstring_explaining_purpose(self):
        """InferenceOutput class should have a meaningful docstring."""
        from coordinator_node.crunch_config import InferenceOutput

        assert InferenceOutput.__doc__ is not None
        assert len(InferenceOutput.__doc__) > 20, (
            "InferenceOutput docstring should explain what it is and how to customize"
        )

    def test_scaffold_has_inline_comment_for_output_type(self):
        """The scaffold CrunchConfig should have an inline comment
        pointing users to customize output_type."""
        content = SCAFFOLD_CONFIG.read_text()
        # Should have a comment next to output_type
        for line in content.splitlines():
            if "output_type" in line and "=" in line:
                assert (
                    "#" in line
                    or "customize"
                    in content[
                        content.index("output_type") : content.index("output_type")
                        + 200
                    ].lower()
                ), (
                    "output_type line should have an inline comment guiding customization"
                )
                return
        # If we get here, output_type wasn't found at all (caught by #6 test)

    def test_inference_output_fields_documented(self):
        """InferenceOutput fields should have descriptions."""
        from coordinator_node.crunch_config import InferenceOutput

        for name, field in InferenceOutput.model_fields.items():
            assert field.description is not None, (
                f"InferenceOutput.{name} should have a description"
            )
