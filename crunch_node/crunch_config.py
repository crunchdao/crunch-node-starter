from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from crunch_node.entities.feed_record import FeedRecord
from crunch_node.entities.prediction import (
    CruncherReward,
    EmissionCheckpoint,
    InputRecord,
    PredictionRecord,
    ProviderReward,
    SnapshotRecord,
)
from crunch_node.feeds.contracts import FeedDataRecord


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


# ── Callable protocols ──────────────────────────────────────────────


@runtime_checkable
class ScoringFunction(Protocol):
    """Score a single prediction against ground truth.

    Packs should define a narrower Protocol with their concrete types::

        class TradingScoringFunction(Protocol):
            def __call__(
                self, prediction: InferenceOutput, ground_truth: GroundTruth
            ) -> ScoreResult: ...

    The engine coerces raw dicts into typed objects before calling.
    """

    def __call__(self, prediction: BaseModel, ground_truth: BaseModel) -> BaseModel: ...


@runtime_checkable
class ResolveGroundTruth(Protocol):
    """Resolve ground truth from feed records for a prediction.

    Args:
        feed_records: Feed records in the resolution window.
        prediction: The prediction being scored (use scope to filter).

    Returns:
        Typed ground truth object, or None if not yet available.
    """

    def __call__(
        self,
        feed_records: list[FeedRecord],
        prediction: PredictionRecord | None = ...,
    ) -> BaseModel | None: ...


@runtime_checkable
class AggregateSnapshot(Protocol):
    """Aggregate a list of score results into a snapshot summary.

    Args:
        score_results: List of ScoreResult dicts from a scoring period.

    Returns:
        Summary dict for the snapshot's result_summary field.
    """

    def __call__(self, score_results: list[dict[str, Any]]) -> dict[str, Any]: ...


@runtime_checkable
class BuildEmission(Protocol):
    """Build an EmissionCheckpoint from ranked leaderboard entries.

    Args:
        ranked_entries: Leaderboard entries sorted by rank, each a dict
            with at least 'rank', 'model_id', and score fields.
        crunch_pubkey: On-chain crunch account public key.
        compute_provider: Compute provider wallet pubkey (optional).
        data_provider: Data provider wallet pubkey (optional).

    Returns:
        EmissionCheckpoint with reward distributions.
    """

    def __call__(
        self,
        ranked_entries: list[dict[str, Any]],
        crunch_pubkey: str,
        compute_provider: str | None = ...,
        data_provider: str | None = ...,
    ) -> EmissionCheckpoint: ...


@runtime_checkable
class ComputeMetrics(Protocol):
    """Compute named metrics from predictions and scores.

    Args:
        metrics: List of metric names to compute (e.g. ["ic", "hit_rate"]).
        predictions: List of prediction dicts with inference_output, scope, etc.
        scores: List of score result dicts.
        context: MetricsContext with model_id, time window, cross-model data.

    Returns:
        Dict mapping metric name to float value.
    """

    def __call__(
        self,
        metrics: list[str],
        predictions: list[dict[str, Any]],
        scores: list[dict[str, Any]],
        context: Any,
    ) -> dict[str, float]: ...


@runtime_checkable
class EnsembleStrategy(Protocol):
    """Compute per-model weights for an ensemble.

    Args:
        model_metrics: Per-model metric dicts (e.g. {"model_1": {"value": 0.5}}).
        predictions: Per-model prediction lists.

    Returns:
        Dict mapping model_id to weight (will be normalized).
    """

    def __call__(
        self,
        model_metrics: dict[str, dict[str, float]],
        predictions: dict[str, list[dict[str, Any]]],
    ) -> dict[str, float]: ...


@runtime_checkable
class EnsembleModelFilter(Protocol):
    """Filter which models participate in an ensemble.

    Args:
        model_id: The model being evaluated.
        metrics: That model's metric dict.

    Returns:
        True to include, False to exclude.
    """

    def __call__(self, model_id: str, metrics: dict[str, float]) -> bool: ...


@runtime_checkable
class PredictionSink(Protocol):
    """Object that intercepts feed ticks and post-prediction results."""

    async def on_record(self, record: FeedDataRecord) -> None: ...

    def on_predictions(
        self,
        predictions: list[PredictionRecord],
        input_record: InputRecord,
        now: datetime,
    ) -> list[PredictionRecord]: ...


@runtime_checkable
class BuildPredictionSink(Protocol):
    """Factory that builds a prediction sink for the predict worker."""

    def __call__(self, *, session: Any, config: CrunchConfig) -> PredictionSink: ...


@runtime_checkable
class BuildScoreSnapshots(Protocol):
    """Factory that returns a snapshot builder for the score worker.

    The returned callable is invoked each scoring cycle with the current
    timestamp and must produce the snapshot records for that cycle.
    """

    def __call__(
        self, *, session: Any, config: CrunchConfig, snapshot_repository: Any
    ) -> Callable[[datetime], list[SnapshotRecord]]: ...


@runtime_checkable
class BuildWidgets(Protocol):
    """Factory that returns dashboard widget descriptors for the report UI.

    Each dict describes a single widget with at minimum a ``"type"`` key.
    """

    def __call__(self) -> list[dict[str, Any]]: ...


class PredictionScope(BaseModel):
    """What defines a single prediction context — passed to model.predict()."""

    model_config = ConfigDict(extra="allow")

    subject: str = Field(
        default="BTCUSDT",
        description=(
            "Asset or topic the model predicts. 'BTCUSDT' is the default — "
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
    """Configuration for a named ensemble (virtual meta-model).

    strategy: Callable that computes per-model weights.
        Signature: (model_metrics, predictions) → {model_id: weight}
    model_filter: Optional callable to select which models participate.
        Signature: (model_id, metrics) → bool
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    strategy: EnsembleStrategy | None = Field(
        default=None,
        description="Weight function: (model_metrics, predictions) → {model_id: weight}",
    )
    model_filter: EnsembleModelFilter | None = Field(
        default=None,
        description="Filter: (model_id, metrics) → bool. Use top_n(5) or min_metric(...).",
    )
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
    """Default resolver: compute price return from entry and resolved feed records.

    Each feed record's ``values`` dict contains flat OHLCV fields
    (``open``, ``high``, ``low``, ``close``, ``volume``) — not a nested
    ``candles_1m`` list.  The normalizer aggregates multiple records into
    ``candles_1m`` for model input, but ``resolve_ground_truth`` works
    with raw ``FeedRecord`` objects.

    For single-record windows (common with short horizons), uses the
    record's open as entry and close as resolved price.

    Args:
        feed_records: All feed records in the resolution window (any subject).
        prediction: The prediction being scored. Use ``prediction.scope`` to
            filter records in multi-asset competitions.

    Override for custom ground truth (VWAP, cross-venue, labels, etc.).
    """
    if not feed_records:
        return None

    entry = feed_records[0]
    resolved = feed_records[-1]

    # Extract prices — each record has flat OHLCV in values
    entry_vals = entry.values or {}
    resolved_vals = resolved.values or {}

    if len(feed_records) == 1:
        # Single record: use open → close of same candle
        entry_price = float(entry_vals.get("open") or entry_vals.get("price") or 0)
        resolved_price = float(entry_vals.get("close") or entry_vals.get("price") or 0)
    else:
        # Multiple records: use close of first → close of last
        entry_price = float(entry_vals.get("close") or entry_vals.get("price") or 0)
        resolved_price = float(
            resolved_vals.get("close") or resolved_vals.get("price") or 0
        )

    if entry_price == 0:
        return None

    profit = (resolved_price - entry_price) / abs(entry_price)

    return {
        "symbol": resolved.subject,
        "asof_ts": int(resolved.ts_event.timestamp() * 1000),
        "entry_price": entry_price,
        "resolved_price": resolved_price,
        "profit": profit,
        "direction_up": resolved_price > entry_price,
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
                scope_key="realtime-btcusdt",
                scope={"subject": "BTCUSDT"},
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
                scope_key="BTCUSDT-60",
                scope={"subject": "BTCUSDT"},
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
    compute_metrics: ComputeMetrics = default_compute_metrics

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

    # Callables — use Protocol types for clear contracts.
    # Packs can narrow these with their own typed Protocols.
    scoring_function: ScoringFunction | None = Field(
        default=None,
        description=(
            "Scoring callable: (prediction, ground_truth) → score_result. "
            "Receives typed Pydantic objects (output_type, ground_truth_type). "
            "Must return an object matching score_type. "
            "If set, takes precedence over the SCORING_FUNCTION env var."
        ),
    )
    resolve_ground_truth: ResolveGroundTruth = default_resolve_ground_truth
    max_ground_truth_staleness_fraction: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description=(
            "Maximum allowed staleness as a fraction of the resolve horizon. "
            "If the last feed record is more than (horizon * fraction) seconds "
            "before the horizon, scoring is skipped. E.g., 0.2 means data must "
            "be within the last 20% of the horizon window. Set to 0 to disable."
        ),
    )
    aggregate_snapshot: AggregateSnapshot = default_aggregate_snapshot
    build_emission: BuildEmission = default_build_emission

    build_prediction_sink: BuildPredictionSink | None = None
    build_score_snapshots: BuildScoreSnapshots | None = None
    build_trading_widgets: BuildWidgets | None = None
    feed_subject_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="Map feed subjects to model-facing names (e.g. BTCUSDT -> BTC)",
    )

    def get_ground_truth_type(self) -> type[BaseModel]:
        """Return the effective ground truth type.

        If ground_truth_type is explicitly set, return it.
        Otherwise, return the base GroundTruth (accepts any fields via extra="allow").
        """
        if self.ground_truth_type is not None:
            return self.ground_truth_type

        return GroundTruth
