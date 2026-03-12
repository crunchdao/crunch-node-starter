"""Tests for model output validation against InferenceOutput schema.

Issue #10: Bad predictions should fail at prediction time, not at scoring.
The validator should reject outputs that are missing required fields or
have wrong types.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from crunch_node.crunch_config import CrunchConfig
from crunch_node.services.predict import PredictService
from crunch_node.services.predict_components import OutputValidator


def _make_service(output_type=None):
    kwargs = {"output_type": output_type} if output_type is not None else {}
    config = CrunchConfig(**kwargs)
    service = PredictService.__new__(PredictService)
    service.config = config
    service.logger = logging.getLogger("test")
    service._output_validator = OutputValidator(
        output_type=config.output_type,
        logger=service.logger,
    )
    return service


class TestOutputValidationRejectsInvalidOutput:
    """validate_output should catch schema violations at prediction time."""

    def test_rejects_missing_required_field(self):
        """If InferenceOutput has a required field (no default), empty dict fails."""

        class StrictOutput(BaseModel):
            direction: str  # required, no default
            confidence: float  # required, no default

        service = _make_service(output_type=StrictOutput)

        error = service.validate_output({})
        assert error is not None, (
            "validate_output should reject {} when InferenceOutput has required fields"
        )

    def test_rejects_wrong_type(self):
        """If output has a field with wrong type that can't be coerced, it fails."""

        class TypedOutput(BaseModel):
            value: float
            direction: str

        service = _make_service(output_type=TypedOutput)

        # Pass a dict where direction is a list instead of str
        error = service.validate_output({"value": 1.0, "direction": ["invalid"]})
        assert error is not None

    def test_accepts_valid_output(self):
        """Valid output should pass."""
        service = _make_service()

        error = service.validate_output({"value": 1.5})
        assert error is None

    def test_accepts_output_with_extra_fields(self):
        """Extra fields from model should not cause validation failure."""
        service = _make_service()

        error = service.validate_output({"value": 1.5, "extra_info": "foo"})
        assert error is None

    def test_warns_when_no_output_keys_match_schema(self):
        """If model returns keys that don't match any InferenceOutput field,
        the output is effectively all defaults — likely a bug."""
        service = _make_service()

        error = service.validate_output({"prediction": 1.5, "forecast": "up"})
        assert error is not None, (
            "validate_output should reject output where no keys match "
            "InferenceOutput fields — the model is returning wrong schema"
        )

    def test_validate_output_does_not_mutate_on_failure(self):
        """Failed validation should not leave the output dict in a bad state."""

        class StrictOutput(BaseModel):
            direction: str
            confidence: float

        service = _make_service(output_type=StrictOutput)

        original = {"bad_key": "value"}
        original_copy = dict(original)
        service.validate_output(original)
        assert original == original_copy or "direction" not in original
