"""Momentum: predict that recent trend continues."""

from __future__ import annotations

from starter_challenge.tracker import TrackerBase


class MomentumTracker(TrackerBase):
    """Predicts the next return will match recent momentum."""

    def _predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _closes(self._get_data(subject))
        if len(prices) < 3:
            return {"value": 0.0}

        # Average return over last few candles
        lookback = min(5, len(prices) - 1)
        returns = [
            (prices[i] - prices[i - 1]) / prices[i - 1]
            for i in range(-lookback, 0)
            if prices[i - 1] != 0
        ]
        if not returns:
            return {"value": 0.0}

        avg_return = sum(returns) / len(returns)
        return {"value": round(avg_return, 6)}


def _closes(data):
    if not isinstance(data, dict):
        return []
    candles = data.get("candles_1m", [])
    return [float(c["close"]) for c in candles if isinstance(c, dict) and "close" in c]
