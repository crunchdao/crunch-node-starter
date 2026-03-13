"""CrunchConfig for tournament-style competitions.

Models receive a feature dict and return a price prediction.
Ground truth is resolved via explicit API calls (not feed-based).
Scoring uses MAPE (1 - |pred - actual| / actual).

NOTE: Tournament mode is API-driven, not feed-based. The feed_normalizer
setting is ignored — input data comes directly from the tournament API.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from starter_challenge.scoring import (
    GroundTruth,
    InferenceOutput,
    ScoreResult,
    score_prediction,
)

from crunch_node.crunch_config import (
    Aggregation,
    AggregationWindow,
    CallMethodArg,
    CallMethodConfig,
    ScoringFunction,
)
from crunch_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)
from crunch_node.services.tournament_predict import TournamentPredictService


class TournamentInput(BaseModel):
    """What the tournament API provides. Feature dict for each sample."""

    model_config = ConfigDict(extra="allow")

    features: dict[str, float] = Field(default_factory=dict)


class CrunchConfig(BaseCrunchConfig):
    """Tournament-style competition configuration.

    Models are called per-sample via the tournament API. Each model
    receives one feature dict as a JSON argument and returns a predicted
    price. ``run_inference`` loops over all features.

    No feed, no scheduled predictions — rounds are API-driven.
    """

    predict_service_class: type = TournamentPredictService

    input_type: type[BaseModel] = TournamentInput
    ground_truth_type: type[BaseModel] = GroundTruth
    output_type: type[BaseModel] = InferenceOutput
    score_type: type[BaseModel] = ScoreResult

    scoring_function: ScoringFunction = score_prediction

    call_method: CallMethodConfig = Field(
        default_factory=lambda: CallMethodConfig(
            method="predict",
            args=[CallMethodArg(name="features", type="JSON")],
        )
    )

    scheduled_predictions: list = Field(default_factory=list)

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
            "mape",
            "hit_rate",
            "max_drawdown",
        ]
    )
