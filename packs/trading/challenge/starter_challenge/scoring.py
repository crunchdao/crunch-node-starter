"""Trading scoring placeholder.

Actual PnL scoring for trading packs is handled by the TradingEngine.
This function exists to satisfy the scoring function contract.

Return a dict matching your contract's ScoreResult shape.
"""

from __future__ import annotations


def score_prediction(prediction, ground_truth):
    return {"value": 0.0, "success": True, "failed_reason": None}
