"""Mean reversion: predict price reverts to recent average."""

from __future__ import annotations

from starter_challenge.cruncher import BaseClass


class MeanReversionTracker(BaseClass):
    """Predicts return toward the rolling mean price."""

    def _predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _closes(self._get_data(subject))
        if len(prices) < 3:
            return {"value": 0.0}

        lookback = min(20, len(prices))
        mean_price = sum(prices[-lookback:]) / lookback
        current = prices[-1]

        if current == 0:
            return {"value": 0.0}

        # Expected return = direction toward mean, scaled down
        expected_return = (mean_price - current) / current
        return {"value": round(expected_return, 6)}


def _closes(data):
    if not isinstance(data, dict):
        return []
    candles = data.get("candles_1m", [])
    return [float(c["close"]) for c in candles if isinstance(c, dict) and "close" in c]
