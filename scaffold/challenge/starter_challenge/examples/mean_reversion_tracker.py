from __future__ import annotations

from starter_challenge.cruncher import BaseClass


class MeanReversionTracker(BaseClass):
    """Predicts pullback after short-term overextension."""

    def _predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _extract_prices(self._get_data(subject))
        if len(prices) < 3:
            return {"value": 0.0}

        lookback = min(5, len(prices))
        window = prices[-lookback:]
        average = sum(window) / lookback
        deviation = (window[-1] - average) / max(abs(average), 1e-9)

        return {"value": -0.7 * deviation}


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
