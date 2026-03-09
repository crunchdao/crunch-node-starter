"""Momentum-based prediction: projects recent trend as a directional value."""

from __future__ import annotations

from starter_challenge.tracker import TrackerBase


class MomentumTracker(TrackerBase):
    """Outputs a prediction proportional to recent price momentum."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _extract_prices(self._get_data(subject))
        if len(prices) < 3:
            return {"value": 0.0}

        lookback = min(8, len(prices))
        window = prices[-lookback:]
        momentum = (window[-1] - window[0]) / max(abs(window[0]), 1e-9)

        # Scale momentum to a conviction value (cap at ~5% move)
        value = max(-1.0, min(1.0, momentum * 20.0))
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
