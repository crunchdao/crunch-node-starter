"""CrunchConfig for trading signal competitions.

Models receive live OHLCV candle data and return a directional signal
in [-1, 1] per trade pair. Scoring simulates leveraged PnL with spread fees.

Feed: Binance-style 1m candles for configurable pairs (default BTCUSDT, ETHUSDT)
Output: signal float in [-1, 1] (positive=long, negative=short, magnitude=conviction)
Scoring: pnl = signal * actual_return - |signal| * spread_fee
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from crunch_node.crunch_config import (
    Aggregation,
    AggregationWindow,
    ScheduledPrediction,
)
from crunch_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)
from crunch_node.services.realtime_predict import RealtimeServiceConfig
from crunch_node.services.trading.costs import CostModel

# ── Type contracts ──────────────────────────────────────────────────
# Input shape is defined by feed_normalizer="candle" → CandleInput
# See crunch_node.feeds.normalizers.candle for the schema.
# Uses default resolve_ground_truth which returns candle data.

SPREAD_FEE = 0.0001  # 1 basis point spread cost


class GroundTruth(BaseModel):
    """Actuals: candle data from entry and resolution times.

    The default resolve_ground_truth returns both entry (at prediction time)
    and resolved (at horizon time) candles so scoring can compute PnL.
    """

    model_config = ConfigDict(extra="allow")

    symbol: str = "BTCUSDT"
    asof_ts: int = 0
    entry_candles_1m: list[dict] = Field(default_factory=list)
    resolved_candles_1m: list[dict] = Field(default_factory=list)


class InferenceOutput(BaseModel):
    """What models must return: a directional trading signal.

    signal: float in [-1.0, 1.0]
      - Positive = LONG (expect price increase)
      - Negative = SHORT (expect price decrease)
      - Zero = FLAT (no position)
      - Magnitude = conviction / leverage scaling
    """

    signal: float = Field(default=0.0, ge=-1.0, le=1.0)


class ScoreResult(BaseModel):
    """Per-prediction score output. PnL-based with spread cost."""

    model_config = ConfigDict(extra="allow")

    value: float = 0.0
    pnl: float = 0.0
    spread_cost: float = 0.0
    actual_return: float = 0.0
    signal_clamped: float = 0.0
    direction_correct: bool = False
    success: bool = True
    failed_reason: str | None = None


def score_prediction(
    prediction: InferenceOutput,
    ground_truth: GroundTruth,
) -> ScoreResult:
    """Score a trading signal against candle-based ground truth.

    PnL = signal * actual_return - |signal| * spread_fee
    """
    if not prediction.signal and prediction.signal != 0.0:
        return ScoreResult(
            success=False,
            failed_reason=f"Invalid signal: {prediction.signal!r}",
        )

    if not ground_truth.entry_candles_1m or not ground_truth.resolved_candles_1m:
        return ScoreResult(
            success=False,
            failed_reason="missing entry or resolved candles",
        )

    entry_price = ground_truth.entry_candles_1m[-1].get("close", 0.0)
    resolved_price = ground_truth.resolved_candles_1m[-1].get("close", 0.0)

    if entry_price == 0:
        return ScoreResult(
            success=False,
            failed_reason="entry price is zero",
        )

    actual_return = (resolved_price - entry_price) / entry_price
    signal_clamped = max(-1.0, min(1.0, prediction.signal))

    spread_cost = abs(signal_clamped) * SPREAD_FEE
    pnl = signal_clamped * actual_return - spread_cost
    direction_correct = (signal_clamped > 0) == (actual_return > 0)

    return ScoreResult(
        value=pnl,
        pnl=pnl,
        spread_cost=spread_cost,
        actual_return=actual_return,
        signal_clamped=signal_clamped,
        direction_correct=direction_correct,
    )


# ── Typed scoring protocol ──────────────────────────────────────────


class TradingScoringFunction(Protocol):
    """Typed scoring contract for trading competitions.

    Scoring functions must accept the pack's concrete types.
    """

    def __call__(
        self, prediction: InferenceOutput, ground_truth: GroundTruth
    ) -> ScoreResult: ...


# ── CrunchConfig ────────────────────────────────────────────────────


class CrunchConfig(BaseCrunchConfig):
    """Trading signal competition configuration.

    Multi-asset signal prediction with PnL scoring.
    Models return signal [-1, 1], scored on realized return minus spread.

    Input shape: CandleInput {symbol, asof_ts, candles_1m: [Candle]}
    """

    feed_normalizer: str = "candle"
    ground_truth_type: type[BaseModel] = GroundTruth
    output_type: type[BaseModel] = InferenceOutput
    score_type: type[BaseModel] = ScoreResult

    # Realtime service hooks (pre/post feed_update lifecycle)
    realtime_service: RealtimeServiceConfig = Field(
        default_factory=RealtimeServiceConfig
    )

    scoring_function: Callable[..., Any] = score_prediction  # type: ignore[assignment]

    cost_model: CostModel = Field(default_factory=CostModel)

    aggregation: Aggregation = Field(
        default_factory=lambda: Aggregation(
            windows={
                "score_recent": AggregationWindow(hours=24),
                "score_steady": AggregationWindow(hours=72),
                "score_anchor": AggregationWindow(hours=168),
            },
            value_field="net_pnl",
            ranking_key="score_recent",
            ranking_direction="desc",
        )
    )

    metrics: list[str] = Field(
        default_factory=lambda: [
            "ic",
            "ic_sharpe",
            "hit_rate",
            "mean_return",
            "max_drawdown",
            "sortino_ratio",
            "turnover",
        ]
    )

    scheduled_predictions: list[ScheduledPrediction] = Field(
        default_factory=lambda: [
            ScheduledPrediction(
                scope_key="trading-btcusdt-60s",
                scope={"subject": "BTCUSDT"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=60,
            ),
            ScheduledPrediction(
                scope_key="trading-ethusdt-60s",
                scope={"subject": "ETHUSDT"},
                prediction_interval_seconds=15,
                resolve_horizon_seconds=60,
            ),
        ]
    )
