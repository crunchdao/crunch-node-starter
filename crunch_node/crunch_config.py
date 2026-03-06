from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from crunch_node.entities.feed_record import FeedRecord
from crunch_node.entities.prediction import (
    CruncherReward,
    EmissionCheckpoint,
    InputRecord,
    PredictionRecord,
    ProviderReward,
)


class Meta(BaseModel):
    """Untyped by default. Override to add structured metadata with defaults."""

    model_config = ConfigDict(extra="allow")


class GroundTruth(BaseModel):
    """What the actual outcome looks like for scoring.

    This is computed by resolve_ground_truth(), not raw feed data.
    Override to define your scoring ground truth shape.
    """

    model_config = ConfigDict(extra="allow")


class InferenceOutput(BaseModel):
    """What models must return. Customize fields to match your prediction format.

    The default schema has a single 'value' float — suitable for directional
    predictions (positive=up, negative=down). For richer predictions, replace
    this class entirely::

        class InferenceOutput(BaseModel):
            direction: str = "hold"       # "long", "short", "hold"
            confidence: float = 0.0       # 0.0 to 1.0
            size: float = 0.0             # position size

    Then set ``output_type = InferenceOutput`` in your CrunchConfig.
    """

    value: float = Field(
        default=0.0,
        description=(
            "Prediction value. Positive=up, negative=down, magnitude=confidence. "
            "Replace this field (or the whole class) for your prediction format."
        ),
    )


class ScoreResult(BaseModel):
    """What scoring produces. Customize metrics fields for your challenge.

    The default schema has a single 'value' float plus success/failure tracking.
    Extra fields from the scoring function (e.g. actual_return, direction_correct)
    are preserved in the DB via ``extra="allow"``, enabling richer analysis.

    Example — custom ScoreResult with additional metrics::

        class ScoreResult(BaseModel):
            model_config = ConfigDict(extra="allow")
            value: float = 0.0
            pnl: float = 0.0
            sharpe: float = 0.0
            max_drawdown: float = 0.0
            success: bool = True
            failed_reason: str | None = None

    Then set ``score_type = ScoreResult`` in your CrunchConfig.
    """

    model_config = ConfigDict(extra="allow")

    value: float = Field(
        default=0.0,
        description=(
            "Primary score value used for ranking. Higher = better by default "
            "(configure Aggregation.ranking_direction to change). "
            "Your scoring function should set this to a meaningful metric."
        ),
    )
    success: bool = Field(
        default=True,
        description=(
            "Whether scoring succeeded. Set to False when scoring fails "
            "(e.g. missing data, invalid prediction). Failed scores are "
            "excluded from aggregation."
        ),
    )
    failed_reason: str | None = Field(
        default=None,
        description="Human-readable reason when success=False.",
    )


class PredictionScope(BaseModel):
    """What defines a single prediction context — passed to model.predict()."""

    model_config = ConfigDict(extra="allow")

    subject: str = Field(
        default="BTC",
        description=(
            "Asset or topic the model predicts. 'BTC' is an example — "
            "replace with your competition's subject(s). For multi-asset, "
            "use separate prediction configs per subject."
        ),
    )
    step_seconds: int = Field(
        default=15,
        ge=1,
        description=(
            "Time granularity within a prediction horizon (seconds). "
            "Passed to model.predict() as context — NOT the scheduling interval. "
            "Example: with resolve_horizon=60 and step=15, the model knows "
            "it's producing a 60s forecast at 15s resolution."
        ),
    )


class CallMethodArg(BaseModel):
    """A single argument to pass when calling a model method."""

    name: str = Field(description="Scope key to read the value from")
    type: str = Field(
        default="STRING", description="VariantType: STRING, INT, FLOAT, JSON"
    )


class CallMethodConfig(BaseModel):
    """How the coordinator invokes models.

    Default: ``predict(subject, resolve_horizon_seconds, step_seconds)``
    Override for competitions that use a different method signature, e.g.::

        CallMethodConfig(method="trade", args=[
            CallMethodArg(name="symbol", type="STRING"),
            CallMethodArg(name="side", type="STRING"),
        ])
    """

    method: str = Field(
        default="predict", description="gRPC method name to call on models"
    )
    args: list[CallMethodArg] = Field(
        default_factory=lambda: [
            CallMethodArg(name="subject", type="STRING"),
            CallMethodArg(name="resolve_horizon_seconds", type="INT"),
            CallMethodArg(name="step_seconds", type="INT"),
        ],
        description="Ordered list of arguments extracted from the prediction scope",
    )


class AggregationWindow(BaseModel):
    """A rolling time window for score aggregation.

    Only ``hours`` is accepted.
    """

    model_config = ConfigDict(extra="forbid")

    hours: int = Field(ge=1)


class Aggregation(BaseModel):
    """How scores are rolled up per model and how the leaderboard is ranked.

    ``windows`` is a **dict** keyed by window name
    The ranking field is ``ranking_direction``
    """

    model_config = ConfigDict(extra="forbid")

    windows: dict[str, AggregationWindow] = Field(
        default_factory=lambda: {
            "score_recent": AggregationWindow(hours=24),
            "score_steady": AggregationWindow(hours=72),
            "score_anchor": AggregationWindow(hours=168),
        }
    )
    value_field: str = Field(
        default="value",
        description=(
            "Score field name to extract from each snapshot's result_summary "
            "for windowed averaging. Must match a numeric key in ScoreResult "
            "(e.g. 'value', 'net_pnl')."
        ),
    )
    ranking_key: str = Field(
        default="score_recent",
        description=(
            "Which key in the final metrics dict to rank by. Can be a window "
            "name (e.g. 'score_recent') or a score field name (e.g. 'net_pnl')."
        ),
    )
    ranking_direction: str = "desc"


class EnsembleConfig(BaseModel):
    """Configuration for a named ensemble (virtual meta-model)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    strategy: Callable = Field(default=None)  # weight function, set below
    model_filter: Callable | None = Field(default=None)
    enabled: bool = True


class PerformanceConfig(BaseModel):
    """Configuration for pipeline timing instrumentation."""

    model_config = ConfigDict(extra="allow")

    # Pipeline timing instrumentation
    timing_enabled: bool = Field(
        default=True,
        description="Enable pipeline timing instrumentation for performance analysis",
    )
    timing_buffer_size: int = Field(
        default=10000,
        ge=100,
        description="Maximum number of timing records to keep in memory buffer",
    )
    timing_endpoint_enabled: bool = Field(
        default=True,
        description="Expose /timing-metrics HTTP endpoint for analysis",
    )


def default_resolve_ground_truth(
    feed_records: list[FeedRecord],
    prediction: PredictionRecord | None = None,
) -> dict[str, Any] | None:
    """Default resolver: return candle data from entry and resolved feed records.

    Returns both entry (first record) and resolved (last record) candles
    so the scorer can compute price return.

    Args:
        feed_records: All feed records in the resolution window (any subject).
        prediction: The prediction being scored. Use ``prediction.scope`` to
            filter records in multi-asset competitions.

    Override for custom ground truth (VWAP, cross-venue, labels, etc.).
    """
    if len(feed_records) < 1:
        return None

    entry = feed_records[0]
    resolved = feed_records[-1]

    return {
        "symbol": resolved.subject,
        "asof_ts": int(resolved.ts_event.timestamp() * 1000),
        "entry_candles_1m": entry.values.get("candles_1m", []),
        "resolved_candles_1m": resolved.values.get("candles_1m", []),
    }


FRAC_64_MULTIPLIER = 1_000_000_000  # 100% in on-chain frac64 representation


def pct_to_frac64(pct: float) -> int:
    """Convert percentage (0-100) to frac64 (0 to FRAC_64_MULTIPLIER)."""
    return int(round(pct / 100.0 * FRAC_64_MULTIPLIER))


def default_build_emission(
    ranked_entries: list[dict[str, Any]],
    crunch_pubkey: str,
    compute_provider: str | None = None,
    data_provider: str | None = None,
) -> EmissionCheckpoint:
    """Build an EmissionCheckpoint from ranked entries.

    Default tier distribution (must sum to 100%):
      1st = 35%, 2nd-5th = 10% each, 6th-10th = 5% each, rest split equally.

    All cruncher reward_pcts must sum to exactly FRAC_64_MULTIPLIER.
    Compute/data provider rewards default to 100% for a single provider.
    """
    # Tier definition: (rank_start, rank_end_inclusive, pct_of_100)
    tiers: list[tuple[int, int, float]] = [
        (1, 1, 35.0),
        (2, 5, 10.0),
        (6, 10, 5.0),
    ]

    # Assign raw percentages by tier
    raw_pcts: list[float] = []
    for entry in ranked_entries:
        rank = entry.get("rank", 0)
        pct = 0.0
        for start, end, tier_pct in tiers:
            if start <= rank <= end:
                pct = tier_pct
                break
        raw_pcts.append(pct)

    # Redistribute unclaimed to ensure sum = 100%
    total_raw = sum(raw_pcts)
    if total_raw < 100.0 and len(ranked_entries) > 0:
        # Split remainder equally among all participants
        remainder_each = (100.0 - total_raw) / len(ranked_entries)
        raw_pcts = [p + remainder_each for p in raw_pcts]

    # Convert to frac64, ensuring exact sum = FRAC_64_MULTIPLIER
    frac64_values = [pct_to_frac64(p) for p in raw_pcts]
    if frac64_values:
        diff = FRAC_64_MULTIPLIER - sum(frac64_values)
        frac64_values[0] += diff  # adjust first entry for rounding

    cruncher_rewards: list[CruncherReward] = []
    for i, entry in enumerate(ranked_entries):
        cruncher_rewards.append(
            CruncherReward(
                cruncher_index=i,
                reward_pct=frac64_values[i],
            )
        )

    # Default: single compute + data provider each get 100%
    compute_rewards: list[ProviderReward] = []
    if compute_provider:
        compute_rewards.append(
            ProviderReward(
                provider=compute_provider,
                reward_pct=FRAC_64_MULTIPLIER,
            )
        )

    data_rewards: list[ProviderReward] = []
    if data_provider:
        data_rewards.append(
            ProviderReward(
                provider=data_provider,
                reward_pct=FRAC_64_MULTIPLIER,
            )
        )

    return EmissionCheckpoint(
        crunch=crunch_pubkey,
        cruncher_rewards=cruncher_rewards,
        compute_provider_rewards=compute_rewards,
        data_provider_rewards=data_rewards,
    )


def default_aggregate_snapshot(score_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Default aggregator: average all numeric fields from score results in the period.

    Iterates over ALL keys in each score result dict (not just 'value'),
    so custom ScoreResult fields (net_pnl, drawdown_pct, etc.) are preserved
    in the snapshot result_summary and flow through to the leaderboard.
    Non-numeric fields (str, bool, None) are taken from the latest result.
    """
    if not score_results:
        return {}

    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    latest_non_numeric: dict[str, Any] = {}

    for result in score_results:
        for key, value in result.items():
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0.0) + float(value)
                counts[key] = counts.get(key, 0) + 1
            elif value is not None:
                latest_non_numeric[key] = value

    summary = {key: totals[key] / counts[key] for key in totals}
    # Include non-numeric fields from latest result (e.g. failed_reason)
    for key, value in latest_non_numeric.items():
        if key not in summary:
            summary[key] = value
    return summary


def default_compute_metrics(
    metrics: list[str],
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: Any,
) -> dict[str, float]:
    """Default metrics computation using the global metrics registry."""
    from crunch_node.metrics.registry import get_default_registry

    return get_default_registry().compute(metrics, predictions, scores, context)


class ScheduledPrediction(BaseModel):
    """A single scheduled prediction entry — defines what, when, and how to predict.

    Replaces the old ``scheduled_prediction_configs.json`` file. Define these
    directly in your CrunchConfig::

        scheduled_predictions = [
            ScheduledPrediction(
                scope_key="realtime-btc",
                scope={"subject": "BTC"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=60,
            ),
        ]
    """

    model_config = ConfigDict(extra="allow")

    scope_key: str = Field(
        min_length=1, description="Unique key for this prediction scope"
    )
    scope: dict[str, Any] = Field(
        default_factory=dict,
        description="Scope template dict passed to model.predict() — e.g. subject",
    )
    prediction_interval_seconds: int = Field(
        default=60,
        ge=1,
        description="How often the coordinator calls models (seconds)",
    )
    resolve_horizon_seconds: int = Field(
        default=0,
        ge=0,
        description="Seconds to wait before resolving ground truth. 0 = immediate (live trading).",
    )
    active: bool = Field(
        default=True, description="Whether this prediction config is active"
    )
    order: int = Field(default=0, description="Display/processing order")
    meta: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")


class CrunchConfig(BaseModel):
    """Single source of truth for challenge data shapes and aggregation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    meta_type: type[BaseModel] = Meta
    feed_normalizer: str = Field(
        default="candle",
        description=(
            "Normalizer name that defines the input shape models receive. "
            "The normalizer transforms feed records into a Pydantic model. "
            "Available: 'candle' (OHLCV), 'tick' (price ticks). "
            "Use get_normalizer(config.feed_normalizer).output_type to get the input schema."
        ),
    )
    input_type: type[BaseModel] | None = Field(
        default=None,
        description=(
            "Optional input schema for non-feed modes (e.g., tournament API). "
            "When set, used to validate API-provided inputs. "
            "For feed-based modes, use feed_normalizer instead."
        ),
    )
    ground_truth_type: type[BaseModel] | None = Field(
        default=None,
        description=(
            "Ground truth schema for scoring. When None (default), derived from "
            "the feed normalizer's output_type. Set explicitly for API-driven "
            "modes (tournament) or when ground truth differs from input shape."
        ),
    )
    output_type: type[BaseModel] = InferenceOutput
    score_type: type[BaseModel] = ScoreResult
    scope: PredictionScope = Field(default_factory=PredictionScope)
    call_method: CallMethodConfig = Field(default_factory=CallMethodConfig)
    aggregation: Aggregation = Field(default_factory=Aggregation)

    # Scheduled predictions — replaces scheduled_prediction_configs.json
    scheduled_predictions: list[ScheduledPrediction] = Field(
        default_factory=lambda: [
            ScheduledPrediction(
                scope_key="BTC-60",
                scope={"subject": "BTC"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=60,
            ),
        ],
        description="Prediction schedules — what to predict, how often, when to resolve",
    )

    # Multi-metric scoring
    metrics: list[str] = Field(
        default_factory=lambda: [
            "ic",
            "ic_sharpe",
            "hit_rate",
            "max_drawdown",
            "model_correlation",
        ]
    )
    compute_metrics: Callable = default_compute_metrics

    # Ensembles
    ensembles: list[EnsembleConfig] = Field(default_factory=list)

    # On-chain identifiers
    crunch_pubkey: str = Field(
        default="", description="Crunch account pubkey for emission checkpoints"
    )
    compute_provider: str | None = Field(
        default=None, description="Compute provider wallet pubkey"
    )
    data_provider: str | None = Field(
        default=None, description="Data provider wallet pubkey"
    )

    # Service classes — override to swap service implementations
    predict_service_class: type | None = Field(
        default=None,
        description=(
            "PredictService subclass to use for the predict worker. "
            "Defaults to None which means RealtimePredictService. "
            "Set to crunch_node.services.predict.PredictService "
            "for the base (no run loop), or provide your own subclass. "
            "The class must accept the same **kwargs as PredictService.__init__."
        ),
    )

    # Performance monitoring configuration
    performance: PerformanceConfig = Field(
        default_factory=PerformanceConfig,
        description="Performance monitoring and instrumentation configuration",
    )

    # Callables
    scoring_function: (
        Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None
    ) = Field(
        default=None,
        description=(
            "Scoring callable: (prediction_dict, ground_truth_dict) → score_dict. "
            "If set, takes precedence over the SCORING_FUNCTION env var. "
            "Use for stateful scoring (e.g. PositionManager-backed trading)."
        ),
    )
    post_predict_hook: (
        Callable[[list[PredictionRecord], InputRecord, Any], list[PredictionRecord]]
        | None
    ) = Field(
        default=None,
        description=(
            "Hook called after models produce outputs but before predictions "
            "are saved to the database. Receives (predictions, input_record, now) "
            "and returns the (possibly modified) list of PredictionRecords."
        ),
    )
    resolve_ground_truth: Callable[
        [list[FeedRecord], PredictionRecord | None], dict[str, Any] | None
    ] = default_resolve_ground_truth
    aggregate_snapshot: Callable[[list[dict[str, Any]]], dict[str, Any]] = (
        default_aggregate_snapshot
    )
    build_emission: Callable[..., EmissionCheckpoint] = default_build_emission

    def get_ground_truth_type(self) -> type[BaseModel]:
        """Return the effective ground truth type.

        If ground_truth_type is explicitly set, return it.
        Otherwise, return the base GroundTruth (accepts any fields via extra="allow").
        """
        if self.ground_truth_type is not None:
            return self.ground_truth_type

        return GroundTruth
