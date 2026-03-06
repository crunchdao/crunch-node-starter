"""Competition-specific CrunchConfig override.

Imports all base types and defaults from the crunch-node library.
Only defines what's different for this competition.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from crunch_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)
from crunch_node.crunch_config import (
    InferenceOutput,
    ScheduledPrediction,
    ScoreResult,
)

# Input shape is defined by feed_normalizer (default: "candle").
# See crunch_node.feeds.normalizers for available normalizers and their output types:
#   - "candle" → CandleInput {symbol, asof_ts, candles_1m: [Candle]}
#   - "tick"   → TickInput {symbol, asof_ts, ticks: [Tick]}


class GroundTruth(BaseModel):
    """Actuals: same shape as input, resolved after the horizon.

    TODO: This example shows candle fields, but the default resolve_ground_truth()
    returns computed values (entry_price, profit, direction_up). Either override
    resolve_ground_truth() to return candles, or update these fields to match
    what the default resolver produces.
    """

    model_config = ConfigDict(extra="allow")

    symbol: str = "BTCUSDT"
    asof_ts: int = 0
    candles_1m: list[dict] = Field(default_factory=list)


class CrunchConfig(BaseCrunchConfig):
    """Competition config — customize data shapes and scoring.

    Key configuration fields:
      - feed_normalizer:   Which normalizer shapes the input ("candle", "tick")
      - ground_truth_type: What the actual outcome looks like (for scoring)
      - output_type:       What models must return (prediction format)
      - score_type:        What scoring produces (metrics/result fields)

    Customize output_type when your models return something other than
    a single float (e.g. trade orders, multi-field predictions).
    Customize score_type when your scoring produces additional metrics
    beyond the default 'value' field.
    """

    feed_normalizer: str = "candle"
    ground_truth_type: type[BaseModel] = GroundTruth
    output_type: type[BaseModel] = InferenceOutput  # customize for your prediction format
    score_type: type[BaseModel] = ScoreResult  # customize for your scoring metrics

    # Prediction schedule — what to predict, how often, when to resolve
    scheduled_predictions: list[ScheduledPrediction] = [
        ScheduledPrediction(
            scope_key="realtime-btc-price-10s",
            scope={
                "subject": "BTCUSDT"
            },  # passed to model.predict(); feed resolution is automatic
            prediction_interval_seconds=1,  # how often it is called
            resolve_horizon_seconds=10,  # how long to wait for the ground truth
        ),
    ]
