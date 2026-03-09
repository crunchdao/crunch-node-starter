"""CrunchConfig for simple prediction competitions.

Models receive live candle data and return a scalar prediction value.
Scoring compares the prediction direction against realized price movement.

This is the simplest competition format: predict a value every N seconds,
get scored against the next observation after the resolution horizon.

Feed: Binance candle data (symbol, OHLCV, timestamp)
Output: value float (positive=up, negative=down, magnitude=conviction)
Scoring: direction * magnitude — rewards correct direction with conviction
"""

from pydantic import BaseModel, Field
from starter_challenge.scoring import (
    PredictionGroundTruth as GroundTruth,
)
from starter_challenge.scoring import (
    PredictionOutput as InferenceOutput,
)
from starter_challenge.scoring import (
    PredictionScoreResult as ScoreResult,
)
from starter_challenge.scoring import (
    score_prediction,
)

from crunch_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)
from crunch_node.crunch_config import (
    ScheduledPrediction,
)
from crunch_node.services.realtime_predict import RealtimePredictService

# Input shape is defined by feed_normalizer="candle" → CandleInput
# See crunch_node.feeds.normalizers.candle for the schema:
#   CandleInput {symbol, asof_ts, candles_1m: [Candle]}


# ── CrunchConfig ────────────────────────────────────────────────────


class CrunchConfig(BaseCrunchConfig):
    """Simple prediction competition configuration.

    Single asset, fast feedback loop. Predictions every 15s,
    resolved after 60s. Good for getting started.

    Types and scoring are defined in starter_challenge.scoring —
    the single source of truth for the challenge package.

    Input shape: CandleInput {symbol, asof_ts, candles_1m: [Candle]}
    """

    predict_service_class: type = RealtimePredictService

    feed_normalizer: str = "candle"
    ground_truth_type: type[BaseModel] = GroundTruth
    output_type: type[BaseModel] = InferenceOutput
    score_type: type[BaseModel] = ScoreResult

    # Single scoring function — imported from challenge package.
    # No custom resolve_ground_truth — default handles candle feeds correctly.
    scoring_function: type = score_prediction

    scheduled_predictions: list[ScheduledPrediction] = Field(
        default_factory=lambda: [
            ScheduledPrediction(
                scope_key="prediction-btcusdt-60s",
                scope={"subject": "BTCUSDT"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=60,
            ),
        ]
    )
