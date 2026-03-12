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
    Aggregation,
    AggregationWindow,
    # CallMethodArg,        # Uncomment for custom model call signatures
    # CallMethodConfig,     # Uncomment for custom model call signatures
    # EnsembleConfig,       # Uncomment for ensemble configuration
    # PredictionScope,      # Uncomment to customize scope shape
    PerformanceConfig,
    ScheduledPrediction,
)
from crunch_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)
from crunch_node.services.realtime_predict import (
    RealtimePredictService,
    # RealtimeServiceConfig,  # Uncomment for pre/post feed hooks
)

# Input shape is defined by feed_normalizer="candle" → CandleInput
# See crunch_node.feeds.normalizers.candle for the schema:
#   CandleInput {symbol, asof_ts, candles_1m: [Candle]}


# ── CrunchConfig ────────────────────────────────────────────────────


class CrunchConfig(BaseCrunchConfig):
    """Simple prediction competition configuration.

    Single asset, fast feedback loop. Predictions every 15s,
    resolved after 120s. Good for getting started.

    Types and scoring are defined in starter_challenge.scoring —
    the single source of truth for the challenge package.

    Input shape: CandleInput {symbol, asof_ts, candles_1m: [Candle]}
    """

    # ── Service ─────────────────────────────────────────────────────
    predict_service_class: type = RealtimePredictService

    # ── Feed & type shapes ──────────────────────────────────────────
    # feed_normalizer determines the input shape models receive.
    # Available: "candle" (OHLCV candles), "tick" (raw price ticks)
    feed_normalizer: str = "candle"

    ground_truth_type: type[BaseModel] = GroundTruth
    output_type: type[BaseModel] = InferenceOutput
    score_type: type[BaseModel] = ScoreResult

    # ── Scoring ─────────────────────────────────────────────────────
    # Scoring function: (prediction, ground_truth) → ScoreResult.
    # Receives typed Pydantic objects. If set, overrides SCORING_FUNCTION env var.
    scoring_function: type = score_prediction

    # Ground truth resolver: (feed_records, prediction) → ground_truth or None.
    # Default computes price return from first/last feed records in window.
    # Override for custom ground truth (VWAP, cross-venue, multi-asset, etc.)
    # resolve_ground_truth: ResolveGroundTruth = default_resolve_ground_truth

    # Maximum staleness as fraction of resolve horizon.
    # If last feed record is older than (horizon * fraction) before the horizon,
    # scoring is skipped. E.g., 0.2 = data must be in last 20% of window.
    # Set to 0.0 to disable staleness checks.
    max_ground_truth_staleness_fraction: float = 0.2

    # ── Aggregation & ranking ───────────────────────────────────────
    # 1-hour rolling window for leaderboard stability.
    aggregation: Aggregation = Aggregation(
        windows={"score_1h": AggregationWindow(hours=1)},
        value_field="value",
        ranking_key="score_1h",
        ranking_direction="desc",  # "desc" = higher is better, "asc" = lower is better
    )

    # Snapshot aggregator: (score_results) → summary dict.
    # Default averages all numeric fields. Override for custom aggregation.
    # aggregate_snapshot: AggregateSnapshot = default_aggregate_snapshot

    # ── Scheduled predictions ───────────────────────────────────────
    scheduled_predictions: list[ScheduledPrediction] = Field(
        default_factory=lambda: [
            ScheduledPrediction(
                scope_key="prediction-btcusdt-120s",
                scope={"subject": "BTCUSDT"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=120,
            ),
        ]
    )

    # ── Metrics ─────────────────────────────────────────────────────
    # Cross-model metrics computed per scoring round.
    # Available: "ic", "ic_sharpe", "hit_rate", "max_drawdown",
    #            "model_correlation", "mean_return", "sortino_ratio", "turnover"
    # Empty list disables metrics (recommended until enough data accumulates).
    metrics: list[str] = Field(default_factory=list)

    # Custom metrics function: (metrics, predictions, scores, context) → {name: float}
    # Default uses the built-in metrics registry.
    # compute_metrics: ComputeMetrics = default_compute_metrics

    # ── Ensembles ───────────────────────────────────────────────────
    # Virtual meta-models that combine predictions from multiple models.
    # Example:
    #   from crunch_node.crunch_config import EnsembleConfig
    #   from crunch_node.services.ensemble import equal_weight, top_n, min_metric
    #
    #   ensembles = [
    #       EnsembleConfig(
    #           name="top5_equal",
    #           strategy=equal_weight,
    #           model_filter=top_n(5),
    #       ),
    #       EnsembleConfig(
    #           name="quality_filter",
    #           strategy=equal_weight,
    #           model_filter=min_metric("hit_rate", 0.55),
    #       ),
    #   ]

    # ── Model call signature ────────────────────────────────────────
    # How the coordinator invokes models via gRPC.
    # Default: predict(subject, resolve_horizon_seconds, step_seconds)
    # Override for competitions with different method signatures:
    #   call_method = CallMethodConfig(
    #       method="trade",
    #       args=[
    #           CallMethodArg(name="symbol", type="STRING"),
    #           CallMethodArg(name="side", type="STRING"),
    #       ],
    #   )

    # Scope template — defines what's passed to model.predict().
    # Default: PredictionScope(subject="BTCUSDT", step_seconds=15)
    # Override for custom scope fields (extra fields via extra="allow"):
    #   scope = PredictionScope(subject="ETHUSDT", step_seconds=30)

    # ── Realtime service hooks ──────────────────────────────────────
    # Pre/post hooks for the feed_update → predict lifecycle.
    # Useful for position management, risk limits, etc.
    #
    #   from crunch_node.services.realtime_predict import RealtimeServiceConfig
    #
    #   realtime_service: RealtimeServiceConfig = RealtimeServiceConfig(
    #       pre_feed_update_hook=my_pre_hook,     # (input_record, now) → input_record
    #       post_predict_hook=my_post_hook,        # (predictions, input_record, now) → predictions
    #   )

    # ── On-chain / emission ─────────────────────────────────────────
    # crunch_pubkey: str = ""            # Crunch account pubkey for emission checkpoints
    # compute_provider: str | None = None  # Compute provider wallet pubkey
    # data_provider: str | None = None     # Data provider wallet pubkey

    # Emission builder: (ranked_entries, crunch_pubkey, ...) → EmissionCheckpoint
    # Default: tiered distribution (35% 1st, 10% 2nd-5th, 5% 6th-10th, rest split)
    # build_emission: BuildEmission = default_build_emission

    # ── Performance monitoring ──────────────────────────────────────
    performance: PerformanceConfig = PerformanceConfig(
        timing_endpoint_enabled=True,
    )
