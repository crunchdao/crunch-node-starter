"""Trading signal scoring: PnL with spread cost.

score = signal * actual_return - |signal| * spread_fee

Signals outside [-1, 1] are clamped. Direction correctness tracked
as a secondary metric for hit-rate analysis.
"""

from __future__ import annotations

SPREAD_FEE = 0.0002  # 2 bps per trade (round-trip)


def score_prediction(prediction: dict, ground_truth: dict) -> dict:
    """Score a trading signal against realized return.

    Args:
        prediction: Model output, expects ``{"signal": float}``.
        ground_truth: Resolved outcome, expects ``{"profit": float}``.

    Returns:
        Dict matching ScoreResult shape.
    """
    try:
        raw_signal = float(prediction.get("signal", 0.0))
    except (TypeError, ValueError):
        return {
            "value": 0.0,
            "pnl": 0.0,
            "spread_cost": 0.0,
            "actual_return": 0.0,
            "signal_clamped": 0.0,
            "direction_correct": False,
            "success": False,
            "failed_reason": f"Invalid signal: {prediction.get('signal')!r}",
        }

    actual_return = float(ground_truth.get("profit", 0.0))

    # Clamp signal to [-1, 1]
    signal = max(-1.0, min(1.0, raw_signal))

    # PnL = signal * return - |signal| * spread
    spread_cost = abs(signal) * SPREAD_FEE
    pnl = signal * actual_return - spread_cost

    direction_correct = (signal > 0 and actual_return > 0) or (
        signal < 0 and actual_return < 0
    )

    return {
        "value": pnl,
        "pnl": pnl,
        "spread_cost": spread_cost,
        "actual_return": actual_return,
        "signal_clamped": signal,
        "direction_correct": direction_correct,
        "success": True,
        "failed_reason": None,
    }
