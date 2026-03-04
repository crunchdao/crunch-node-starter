"""Tests for model output validation against InferenceOutput schema.

Issue #10: Bad predictions should fail at prediction time, not at scoring.
The validator should reject outputs that are missing required fields or
have wrong types.
"""

from __future__ import annotations

from pydantic import BaseModel


class TestOutputValidationRejectsInvalidOutput:
    """validate_output should catch schema violations at prediction time."""

    def test_rejects_missing_required_field(self):
        """If InferenceOutput has a required field (no default), empty dict fails."""
        from crunch_node.crunch_config import CrunchConfig
        from crunch_node.services.predict import PredictService

        class StrictOutput(BaseModel):
            direction: str  # required, no default
            confidence: float  # required, no default

        config = CrunchConfig(output_type=StrictOutput)
        service = PredictService.__new__(PredictService)
        service.contract = config
        import logging

        service.logger = logging.getLogger("test")

        error = service.validate_output({})
        assert error is not None, (
            "validate_output should reject {} when InferenceOutput has required fields"
        )

    def test_rejects_wrong_type(self):
        """If output has a field with wrong type that can't be coerced, it fails."""
        from crunch_node.crunch_config import CrunchConfig
        from crunch_node.services.predict import PredictService

        class TypedOutput(BaseModel):
            value: float
            direction: str

        config = CrunchConfig(output_type=TypedOutput)
        service = PredictService.__new__(PredictService)
        service.contract = config
        import logging

        service.logger = logging.getLogger("test")

        # Pass a dict where direction is a list instead of str
        error = service.validate_output({"value": 1.0, "direction": ["invalid"]})
        assert error is not None

    def test_accepts_valid_output(self):
        """Valid output should pass."""
        from crunch_node.crunch_config import CrunchConfig
        from crunch_node.services.predict import PredictService

        config = CrunchConfig()  # default InferenceOutput: value: float = 0.0
        service = PredictService.__new__(PredictService)
        service.contract = config
        import logging

        service.logger = logging.getLogger("test")

        error = service.validate_output({"value": 1.5})
        assert error is None

    def test_accepts_output_with_extra_fields(self):
        """Extra fields from model should not cause validation failure."""
        from crunch_node.crunch_config import CrunchConfig
        from crunch_node.services.predict import PredictService

        config = CrunchConfig()
        service = PredictService.__new__(PredictService)
        service.contract = config
        import logging

        service.logger = logging.getLogger("test")

        error = service.validate_output({"value": 1.5, "extra_info": "foo"})
        assert error is None

    def test_warns_when_no_output_keys_match_schema(self):
        """If model returns keys that don't match any InferenceOutput field,
        the output is effectively all defaults — likely a bug."""
        from crunch_node.crunch_config import CrunchConfig
        from crunch_node.services.predict import PredictService

        config = CrunchConfig()  # default InferenceOutput: value: float = 0.0
        service = PredictService.__new__(PredictService)
        service.contract = config
        import logging

        service.logger = logging.getLogger("test")

        # Model returns garbage keys — none match InferenceOutput fields.
        # This should fail validation because the model clearly isn't
        # returning the expected schema.
        error = service.validate_output({"prediction": 1.5, "forecast": "up"})
        assert error is not None, (
            "validate_output should reject output where no keys match "
            "InferenceOutput fields — the model is returning wrong schema"
        )

    def test_validate_output_does_not_mutate_on_failure(self):
        """Failed validation should not leave the output dict in a bad state."""
        from crunch_node.crunch_config import CrunchConfig
        from crunch_node.services.predict import PredictService

        class StrictOutput(BaseModel):
            direction: str
            confidence: float

        config = CrunchConfig(output_type=StrictOutput)
        service = PredictService.__new__(PredictService)
        service.contract = config
        import logging

        service.logger = logging.getLogger("test")

        original = {"bad_key": "value"}
        original_copy = dict(original)
        service.validate_output(original)
        # Original should not be mutated with partial schema fields
        assert original == original_copy or "direction" not in original
