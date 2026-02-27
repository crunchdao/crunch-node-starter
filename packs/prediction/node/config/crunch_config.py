"""CrunchConfig for simple prediction competitions.

Models receive live price data and return a scalar prediction value.
Scoring compares the prediction direction against realized price movement.
Immediate resolution — ground truth comes from the next feed update.

This is the simplest competition format: predict a value every N seconds,
get scored immediately against the next observation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from coordinator_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)
from coordinator_node.crunch_config import (
    ScheduledPrediction,
)

# ── Type contracts ──────────────────────────────────────────────────


class RawInput(BaseModel):
    """What the feed produces. Simple OHLCV candles."""

    model_config = ConfigDict(extra="allow")

    symbol: str = "BTC"
    asof_ts: int = 0

    candles_1m: list[dict] = Field(default_factory=list)


class InferenceInput(RawInput):
    """What models receive — same as RawInput."""

    pass


class GroundTruth(RawInput):
    """Actuals: same shape as RawInput, resolved after the horizon."""

    pass


class InferenceOutput(BaseModel):
    """What models must return: a directional prediction.

    value: float
      - Positive = bullish (expect price increase)
      - Negative = bearish (expect price decrease)
      - Magnitude = conviction
    """

    value: float = Field(
        default=0.0,
        description="Prediction value. Positive=up, negative=down.",
    )


class ScoreResult(BaseModel):
    """Per-prediction score output. Directional accuracy."""

    model_config = ConfigDict(extra="allow")

    value: float = 0.0
    actual_return: float = 0.0
    direction_correct: bool = False
    success: bool = True
    failed_reason: str | None = None


# ── CrunchConfig ────────────────────────────────────────────────────


class CrunchConfig(BaseCrunchConfig):
    """Simple prediction competition configuration.

    Single asset, fast feedback loop. Predictions every 15s,
    resolved after 60s. Good for getting started.
    """

    raw_input_type: type[BaseModel] = RawInput
    ground_truth_type: type[BaseModel] = GroundTruth
    input_type: type[BaseModel] = InferenceInput
    output_type: type[BaseModel] = InferenceOutput
    score_type: type[BaseModel] = ScoreResult

    scheduled_predictions: list[ScheduledPrediction] = Field(
        default_factory=lambda: [
            ScheduledPrediction(
                scope_key="prediction-btc-60s",
                scope={"subject": "BTC"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=60,
            ),
        ]
    )
