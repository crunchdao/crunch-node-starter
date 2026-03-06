"""Tests that backtest harness respects custom InferenceOutput schema.

Issue #9: Backtest harness hardcodes {"value": float} output shape.
When InferenceOutput is customized (e.g. trade orders), the backtest
should validate/coerce outputs using the schema, not hardcode {"value": ...}.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from pydantic import BaseModel


class TestBacktestOutputShape:
    """Backtest harness should use InferenceOutput schema for output coercion."""

    def test_non_dict_output_uses_inference_output_schema(self):
        """When model.predict() returns a non-dict, backtest should coerce
        using InferenceOutput fields, not hardcode {"value": output}."""
        from scaffold.challenge.starter_challenge.backtest import BacktestRunner

        # Model that returns a raw float
        model = MagicMock()
        model.feed_update = MagicMock()
        model.predict = MagicMock(return_value=1.5)

        runner = BacktestRunner(model=model)

        # The coercion should produce a dict matching InferenceOutput
        output = runner._coerce_output(1.5)
        assert isinstance(output, dict)
        assert "value" in output  # default InferenceOutput has "value" field

    def test_error_output_uses_inference_output_defaults(self):
        """When model.predict() raises, the error output should use
        InferenceOutput default values, not hardcode {"value": 0.0}."""
        from scaffold.challenge.starter_challenge.backtest import BacktestRunner

        model = MagicMock()
        runner = BacktestRunner(model=model)

        output = runner._coerce_error_output(Exception("boom"))
        assert isinstance(output, dict)
        assert "_error" in output

    def test_dict_output_validated_against_schema(self):
        """Dict output from model should be validated against InferenceOutput."""
        from scaffold.challenge.starter_challenge.backtest import BacktestRunner

        model = MagicMock()
        runner = BacktestRunner(model=model)

        # Valid dict
        output = runner._coerce_output({"value": 1.5})
        assert output["value"] == 1.5

    def test_custom_output_schema_coercion(self):
        """When InferenceOutput is customized, coercion should use the
        custom schema to build default error outputs."""
        from scaffold.challenge.starter_challenge.backtest import BacktestRunner

        class TradeOutput(BaseModel):
            direction: str = "hold"
            size: float = 0.0

        model = MagicMock()
        runner = BacktestRunner(model=model, output_type=TradeOutput)

        # Error output should have TradeOutput defaults, not {"value": 0.0}
        output = runner._coerce_error_output(Exception("boom"))
        assert "direction" in output
        assert "size" in output
        assert output["direction"] == "hold"
