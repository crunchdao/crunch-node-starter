"""CrunchConfig for simple prediction competitions.

Models receive live tick data and return a scalar prediction value.
Scoring compares the prediction direction against realized price movement.

This is the simplest competition format: predict a value every N seconds,
get scored immediately against the next observation.

Feed: Pyth-style price ticks (symbol, price, timestamp)
Output: value float (positive=up, negative=down, magnitude=conviction)
Scoring: direction * magnitude — rewards correct direction with conviction
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from crunch_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)
from crunch_node.crunch_config import (
    ScheduledPrediction,
)
from crunch_node.entities.feed_record import FeedRecord
from crunch_node.entities.prediction_record import PredictionRecord

# ── Type contracts ──────────────────────────────────────────────────
# Input shape is defined by feed_normalizer="tick" → TickInput
# See crunch_node.feeds.normalizers.tick for the schema.


class GroundTruth(BaseModel):
    """Actuals: tick data from entry and resolution times.

    Contains both entry (at prediction time) and resolved (at horizon time)
    ticks so scoring can compute return.
    """

    model_config = ConfigDict(extra="allow")

    symbol: str = "BTC"
    asof_ts: int = 0
    entry_ticks: list[dict] = Field(default_factory=list)
    resolved_ticks: list[dict] = Field(default_factory=list)


def resolve_ground_truth(
    feed_records: list[FeedRecord],
    prediction: PredictionRecord | None = None,
) -> dict[str, Any] | None:
    """Extract tick data from entry and resolved feed records.

    Returns both entry (first record) and resolved (last record) ticks
    so the scorer can compute price return.
    """
    if not feed_records:
        return None

    entry = feed_records[0]
    resolved = feed_records[-1]

    return {
        "symbol": resolved.subject,
        "asof_ts": int(resolved.ts_event.timestamp() * 1000),
        "entry_ticks": entry.values.get("ticks", []),
        "resolved_ticks": resolved.values.get("ticks", []),
    }


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


def score_prediction(
    prediction: InferenceOutput,
    ground_truth: GroundTruth,
) -> ScoreResult:
    """Score a prediction against tick-based ground truth.

    Computes directional accuracy: did the model predict the right direction?
    Score = sign(prediction) * sign(actual_return) * |prediction|
    """
    if not ground_truth.entry_ticks or not ground_truth.resolved_ticks:
        return ScoreResult(
            success=False,
            failed_reason="missing entry or resolved ticks",
        )

    entry_price = ground_truth.entry_ticks[-1].get("price", 0.0)
    resolved_price = ground_truth.resolved_ticks[-1].get("price", 0.0)

    if entry_price == 0:
        return ScoreResult(
            success=False,
            failed_reason="entry price is zero",
        )

    actual_return = (resolved_price - entry_price) / entry_price
    direction_correct = (prediction.value > 0) == (actual_return > 0)
    score = abs(prediction.value) if direction_correct else -abs(prediction.value)

    return ScoreResult(
        value=score,
        actual_return=actual_return,
        direction_correct=direction_correct,
    )


# ── CrunchConfig ────────────────────────────────────────────────────


class CrunchConfig(BaseCrunchConfig):
    """Simple prediction competition configuration.

    Single asset, fast feedback loop. Predictions every 15s,
    resolved after 60s. Good for getting started.

    Input shape: TickInput {symbol, asof_ts, ticks: [{ts, price}]}
    """

    feed_normalizer: str = "tick"
    ground_truth_type: type[BaseModel] = GroundTruth
    output_type: type[BaseModel] = InferenceOutput
    score_type: type[BaseModel] = ScoreResult

    resolve_ground_truth: Any = resolve_ground_truth
    scoring_function: Any = score_prediction

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
