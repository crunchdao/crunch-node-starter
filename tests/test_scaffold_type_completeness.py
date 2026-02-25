"""Tests that the scaffold's CrunchConfig overrides all customizable types.

Issue #6: Scaffold overrides raw_input_type/input_type but forgets
output_type/score_type, making it easy to miss they need customization.
"""

from __future__ import annotations

from pathlib import Path

SCAFFOLD_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent
    / "scaffold"
    / "node"
    / "config"
    / "crunch_config.py"
)


class TestScaffoldTypeCompleteness:
    """The scaffold crunch_config.py should explicitly set all 5 data-shape types."""

    def test_scaffold_config_exists(self):
        assert SCAFFOLD_CONFIG_PATH.exists(), f"Missing {SCAFFOLD_CONFIG_PATH}"

    def test_scaffold_overrides_output_type(self):
        """output_type must be explicitly set in the scaffold CrunchConfig."""
        source = SCAFFOLD_CONFIG_PATH.read_text()
        assert "output_type" in source, (
            "Scaffold CrunchConfig does not set output_type. Users will miss "
            "that InferenceOutput needs customization for their prediction format."
        )

    def test_scaffold_overrides_score_type(self):
        """score_type must be explicitly set in the scaffold CrunchConfig."""
        source = SCAFFOLD_CONFIG_PATH.read_text()
        assert "score_type" in source, (
            "Scaffold CrunchConfig does not set score_type. Users will miss "
            "that ScoreResult needs customization for their scoring metrics."
        )

    def test_scaffold_imports_inference_output(self):
        """InferenceOutput should be imported in the scaffold."""
        source = SCAFFOLD_CONFIG_PATH.read_text()
        assert "InferenceOutput" in source, (
            "Scaffold should import InferenceOutput so users see it exists"
        )

    def test_scaffold_imports_score_result(self):
        """ScoreResult should be imported in the scaffold."""
        source = SCAFFOLD_CONFIG_PATH.read_text()
        assert "ScoreResult" in source, (
            "Scaffold should import ScoreResult so users see it exists"
        )
