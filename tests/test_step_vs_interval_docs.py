"""Tests that step_seconds and prediction_interval_seconds are documented
as separate concepts.

Issue #8: These two different concepts were set to the same value with no
explanation. The schema docs (field descriptions) must distinguish them.
"""

from __future__ import annotations


class TestStepVsIntervalDocumented:
    """PredictionScope.step_seconds and ScheduleEnvelope.prediction_interval_seconds
    must have distinct, non-empty descriptions."""

    def test_step_seconds_has_description(self):
        from crunch_node.crunch_config import PredictionScope

        field = PredictionScope.model_fields["step_seconds"]
        assert field.description is not None and len(field.description) > 10, (
            "PredictionScope.step_seconds must have a description explaining "
            "what it controls (time granularity within a prediction horizon)"
        )

    def test_prediction_interval_seconds_has_description(self):
        from crunch_node.schemas.payload_contracts import ScheduleEnvelope

        field = ScheduleEnvelope.model_fields["prediction_interval_seconds"]
        assert field.description is not None and len(field.description) > 10, (
            "ScheduleEnvelope.prediction_interval_seconds must have a description "
            "explaining what it controls (how often the coordinator calls models)"
        )

    def test_descriptions_are_different(self):
        from crunch_node.crunch_config import PredictionScope
        from crunch_node.schemas.payload_contracts import ScheduleEnvelope

        step_desc = PredictionScope.model_fields["step_seconds"].description or ""
        interval_desc = (
            ScheduleEnvelope.model_fields["prediction_interval_seconds"].description
            or ""
        )

        assert step_desc != interval_desc, (
            "step_seconds and prediction_interval_seconds must have different "
            "descriptions — they are different concepts"
        )

    def test_resolve_horizon_seconds_has_description(self):
        from crunch_node.schemas.payload_contracts import ScheduleEnvelope

        field = ScheduleEnvelope.model_fields["resolve_horizon_seconds"]
        assert field.description is not None and len(field.description) > 10, (
            "ScheduleEnvelope.resolve_horizon_seconds must have a description"
        )
