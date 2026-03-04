"""Tests that placeholder values in config are clearly marked as examples.

Issue #1: Placeholder values (BTC, pyth, 60s) look like real defaults.
Users copy them into production without changing. Values that MUST be
customized should be clearly marked and/or validated.
"""

from __future__ import annotations

from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".local.env"


class TestPlaceholderDefaults:
    """Scaffold defaults should be clearly documented as examples."""

    def test_local_env_has_placeholder_warnings(self):
        """The .local.env file should document which values are placeholders."""
        assert ENV_FILE.exists(), f"Missing {ENV_FILE}"
        content = ENV_FILE.read_text()
        # Key values that users must customize should have comments
        assert (
            "CHANGE THIS" in content.upper()
            or "PLACEHOLDER" in content.upper()
            or "EXAMPLE" in content.upper()
            or "CUSTOMIZE" in content.upper()
        ), (
            ".local.env should contain comments marking placeholder values "
            "that users must change (CRUNCH_ID, CRUNCH_PUBKEY, FEED_SUBJECTS, etc.)"
        )

    def test_crunch_pubkey_marked_as_placeholder(self):
        """CRUNCH_PUBKEY should be clearly marked as needing replacement."""
        content = ENV_FILE.read_text()
        for line in content.splitlines():
            if line.strip().startswith("CRUNCH_PUBKEY="):
                # The line or surrounding comment should indicate it's a placeholder
                idx = content.splitlines().index(line)
                context = "\n".join(content.splitlines()[max(0, idx - 2) : idx + 2])
                assert any(
                    w in context.upper()
                    for w in ["PLACEHOLDER", "CHANGE", "EXAMPLE", "REPLACE", "YOUR"]
                ), (
                    f"CRUNCH_PUBKEY should be marked as a placeholder that needs replacement. "
                    f"Context: {context}"
                )
                break

    def test_prediction_scope_subject_has_warning(self):
        """PredictionScope.subject default should document it's an example."""
        from crunch_node.crunch_config import PredictionScope

        field = PredictionScope.model_fields["subject"]
        desc = field.description or ""
        assert len(desc) > 0, (
            "PredictionScope.subject should have a description noting 'BTC' "
            "is an example value"
        )
