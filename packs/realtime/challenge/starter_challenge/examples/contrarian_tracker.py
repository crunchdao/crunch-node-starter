"""Contrarian: predict reversal of last candle's move."""

from __future__ import annotations

from starter_challenge.cruncher import BaseClass


class ContrarianTracker(BaseClass):
    """Predicts the opposite return of the most recent candle."""

    def _predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _closes(self._get_data(subject))
        if len(prices) < 2:
            return {"value": 0.0}

        prev, curr = prices[-2], prices[-1]
        if prev == 0:
            return {"value": 0.0}

        # Last candle return, flipped
        last_return = (curr - prev) / prev
        return {"value": round(-last_return, 6)}


def _closes(data):
    if not isinstance(data, dict):
        return []
    candles = data.get("candles_1m", [])
    return [float(c["close"]) for c in candles if isinstance(c, dict) and "close" in c]
