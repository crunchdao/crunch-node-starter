"""Contrarian prediction: fades the last candle's direction."""

from __future__ import annotations

from starter_challenge.tracker import TrackerBase


class ContrarianTracker(TrackerBase):
    """Predicts the opposite direction of the most recent candle move."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _extract_prices(self._get_data(subject))
        if len(prices) < 3:
            return {"value": 0.0}

        # Use the last two closes to determine recent direction
        last_move = prices[-1] - prices[-2]
        if abs(last_move) < 1e-9:
            return {"value": 0.0}

        # Fade the move: if price went up, predict down (and vice versa)
        magnitude = abs(last_move) / max(abs(prices[-2]), 1e-9)
        value = max(-1.0, min(1.0, -last_move / abs(last_move) * magnitude * 10.0))
        return {"value": round(value, 4)}


def _extract_prices(latest_data):
    if isinstance(latest_data, dict) and isinstance(
        latest_data.get("candles_1m"), list
    ):
        return _closes(latest_data["candles_1m"])
    return []


def _closes(candles):
    closes = []
    for row in candles:
        if not isinstance(row, dict):
            continue
        value = row.get("close")
        try:
            closes.append(float(value))
        except Exception:
            continue
    return closes
