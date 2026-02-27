"""CrunchConfig for tournament-style competitions.

Models receive a feature dataset and return a prediction per target.
Ground truth is resolved after a longer horizon (e.g. 1 hour).
Scoring uses IC (information coefficient) as the primary metric.

This is the classic quant-tournament format: submit predictions on a
fixed schedule, wait for resolution, rank by statistical quality.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from coordinator_node.crunch_config import (
    Aggregation,
    AggregationWindow,
    ScheduledPrediction,
)
from coordinator_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)

# ── Type contracts ──────────────────────────────────────────────────


class RawInput(BaseModel):
    """What the feed produces. Feature set with multiple columns."""

    model_config = ConfigDict(extra="allow")

    symbol: str = "BTC"
    asof_ts: int = 0
    round_id: int = 0

    features: dict[str, float] = Field(
        default_factory=dict,
        description="Named feature values for this round.",
    )


class InferenceInput(RawInput):
    """What models receive — same as RawInput."""

    pass


class GroundTruth(BaseModel):
    """Actuals: target values revealed after the horizon."""

    model_config = ConfigDict(extra="allow")

    target: float = Field(
        default=0.0,
        description="The target value the model was trying to predict.",
    )
    entry_price: float = 0.0
    resolved_price: float = 0.0


class InferenceOutput(BaseModel):
    """What models must return: a prediction value.

    prediction: float — unbounded, but should be on a consistent scale.
    Models are ranked by correlation with the target, not absolute accuracy.
    """

    prediction: float = Field(
        default=0.0,
        description="Model's prediction for the target. Ranked by IC.",
    )


class ScoreResult(BaseModel):
    """Per-prediction score output. IC-based tournament scoring."""

    model_config = ConfigDict(extra="allow")

    value: float = 0.0
    prediction: float = 0.0
    target: float = 0.0
    residual: float = 0.0
    success: bool = True
    failed_reason: str | None = None


# ── CrunchConfig ────────────────────────────────────────────────────


class CrunchConfig(BaseCrunchConfig):
    """Tournament-style competition configuration.

    Longer horizon, batch resolution, IC-based ranking.
    Predictions submitted every 5 minutes, resolved after 1 hour.
    """

    raw_input_type: type[BaseModel] = RawInput
    ground_truth_type: type[BaseModel] = GroundTruth
    input_type: type[BaseModel] = InferenceInput
    output_type: type[BaseModel] = InferenceOutput
    score_type: type[BaseModel] = ScoreResult

    aggregation: Aggregation = Field(
        default_factory=lambda: Aggregation(
            windows={
                "score_recent": AggregationWindow(hours=24),
                "score_steady": AggregationWindow(hours=72),
                "score_anchor": AggregationWindow(hours=168),
            },
            value_field="value",
            ranking_key="score_recent",
            ranking_direction="desc",
        )
    )

    metrics: list[str] = Field(
        default_factory=lambda: [
            "ic",
            "ic_sharpe",
            "hit_rate",
            "max_drawdown",
            "model_correlation",
        ]
    )

    scheduled_predictions: list[ScheduledPrediction] = Field(
        default_factory=lambda: [
            ScheduledPrediction(
                scope_key="tournament-btc-1h",
                scope={"subject": "BTC"},
                prediction_interval_seconds=300,  # every 5 minutes
                resolve_horizon_seconds=3600,  # resolved after 1 hour
            ),
        ]
    )
