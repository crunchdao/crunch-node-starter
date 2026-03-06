"""CrunchConfig for simple prediction competitions.

Models receive live price data and return a scalar prediction value.
Scoring compares the prediction direction against realized price movement.
Immediate resolution — ground truth comes from the next feed update.

This is the simplest competition format: predict a value every N seconds,
get scored immediately against the next observation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from crunch_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)
from crunch_node.crunch_config import (
    ScheduledPrediction,
)

# ── Type contracts ──────────────────────────────────────────────────
# Input shape is defined by feed_normalizer="candle" → CandleInput
# See crunch_node.feeds.normalizers.candle for the schema.


class GroundTruth(BaseModel):
    """Actuals: same shape as input, resolved after the horizon.

    TODO: This example shows candle fields, but the default resolve_ground_truth()
    returns computed values (entry_price, profit, direction_up). Either override
    resolve_ground_truth() to return candles, or update these fields to match
    what the default resolver produces.
    """

    model_config = ConfigDict(extra="allow")

    symbol: str = "BTC"
    asof_ts: int = 0
    candles_1m: list[dict] = Field(default_factory=list)


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

    Input shape: CandleInput {symbol, asof_ts, candles_1m: [Candle]}
    """

    feed_normalizer: str = "candle"
    ground_truth_type: type[BaseModel] = GroundTruth
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
