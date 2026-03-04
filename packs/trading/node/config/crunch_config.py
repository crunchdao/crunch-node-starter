"""CrunchConfig for trading signal competitions.

Models receive live OHLCV candle data and return a directional signal
in [-1, 1] per trade pair. Scoring simulates leveraged PnL with spread fees.

Feed: Binance-style 1m candles for configurable pairs (default BTCUSDT, ETHUSDT)
Output: signal float in [-1, 1] (positive=long, negative=short, magnitude=conviction)
Scoring: pnl = signal * actual_return - |signal| * spread_fee
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from crunch_node.crunch_config import (
    Aggregation,
    AggregationWindow,
    ScheduledPrediction,
)
from crunch_node.crunch_config import (
    CrunchConfig as BaseCrunchConfig,
)

# ── Type contracts ──────────────────────────────────────────────────


class RawInput(BaseModel):
    """What the feed produces. Multi-timeframe OHLCV from exchange."""

    model_config = ConfigDict(extra="allow")

    symbol: str = "BTCUSDT"
    asof_ts: int = 0

    candles_1m: list[dict] = Field(default_factory=list)
    candles_5m: list[dict] = Field(default_factory=list)
    candles_15m: list[dict] = Field(default_factory=list)
    candles_1h: list[dict] = Field(default_factory=list)


class InferenceInput(RawInput):
    """What models receive — identical to RawInput (all market data exposed)."""

    pass


class GroundTruth(RawInput):
    """Actuals: same shape as RawInput, resolved after the horizon."""

    pass


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


# ── CrunchConfig ────────────────────────────────────────────────────


class CrunchConfig(BaseCrunchConfig):
    """Trading signal competition configuration.

    Multi-asset signal prediction with PnL scoring.
    Models return signal [-1, 1], scored on realized return minus spread.
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
            value_field="pnl",
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
