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
    GroundTruth,
    InferenceOutput,
    ScheduledPrediction,
    ScoreResult,
)


class RawInput(BaseModel):
    """What the feed produces. 1m OHLCV candles."""

    model_config = ConfigDict(extra="allow")

    symbol: str = "BTCUSDT"
    asof_ts: int = 0

    candles_1m: list[dict] = Field(default_factory=list)


class InferenceInput(RawInput):
    """What models receive. Same as RawInput unless you override.

    To transform market data before it reaches models, define a different
    shape here and provide a transform function:

        class InferenceInput(BaseModel):
            symbol: str
            momentum: float

        def transform(market: RawInput) -> InferenceInput:
            candles = market.candles_1m
            momentum = candles[-1]["close"] - candles[0]["close"] if candles else 0.0
            return InferenceInput(symbol=market.symbol, momentum=momentum)
    """

    pass


class CrunchConfig(BaseCrunchConfig):
    """Competition config — overrides all data-shape types.

    All five types are listed explicitly so you see what needs customization:
      - raw_input_type:    What the feed produces (market data shape)
      - ground_truth_type: What the actual outcome looks like
      - input_type:        What models receive (can transform from RawInput)
      - output_type:       What models must return (prediction format)
      - score_type:        What scoring produces (metrics/result fields)

    Customize output_type when your models return something other than
    a single float (e.g. trade orders, multi-field predictions).
    Customize score_type when your scoring produces additional metrics
    beyond the default 'value' field.
    """

    raw_input_type: type[BaseModel] = RawInput
    ground_truth_type: type[BaseModel] = GroundTruth
    input_type: type[BaseModel] = InferenceInput
    output_type: type[BaseModel] = (
        InferenceOutput  # ← customize for your prediction format
    )
    score_type: type[BaseModel] = ScoreResult  # ← customize for your scoring metrics

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
