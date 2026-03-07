"""Trading signal scoring: PnL with spread cost.

score = signal * actual_return - |signal| * spread_fee

Signals outside [-1, 1] are clamped. Direction correctness tracked
as a secondary metric for hit-rate analysis.

NOTE: This is a standalone version for the challenge package.
The canonical typed version lives in the pack's crunch_config.py.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

SPREAD_FEE = 0.0002  # 2 bps per trade (round-trip)


class TradingOutput(BaseModel):
    """Model output: directional signal."""

    signal: float = Field(default=0.0, ge=-1.0, le=1.0)


class TradingGroundTruth(BaseModel):
    """Ground truth: realized profit."""

    model_config = ConfigDict(extra="allow")
    profit: float = 0.0


class TradingScoreResult(BaseModel):
    """Score output: PnL with spread cost."""

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
    prediction: TradingOutput, ground_truth: TradingGroundTruth
) -> TradingScoreResult:
    """Score a trading signal against realized return.

    Args:
        prediction: Model output with ``signal`` field.
        ground_truth: Resolved outcome with ``profit`` field.

    Returns:
        TradingScoreResult with PnL metrics.
    """
    actual_return = ground_truth.profit
    signal_clamped = max(-1.0, min(1.0, prediction.signal))

    spread_cost = abs(signal_clamped) * SPREAD_FEE
    pnl = signal_clamped * actual_return - spread_cost

    direction_correct = (signal_clamped > 0 and actual_return > 0) or (
        signal_clamped < 0 and actual_return < 0
    )

    return TradingScoreResult(
        value=pnl,
        pnl=pnl,
        spread_cost=spread_cost,
        actual_return=actual_return,
        signal_clamped=signal_clamped,
        direction_correct=direction_correct,
    )
