"""Trading scoring placeholder.

Actual PnL scoring for trading packs is handled by the TradingEngine.
This function exists to satisfy the scoring function contract.

Return a dict matching your contract's ScoreResult shape.
"""

from __future__ import annotations

from typing import Any


def score_prediction(
    prediction: dict[str, Any], ground_truth: dict[str, Any]
) -> dict[str, float | bool | None]:
    return {"value": 0.0, "success": True, "failed_reason": None}
