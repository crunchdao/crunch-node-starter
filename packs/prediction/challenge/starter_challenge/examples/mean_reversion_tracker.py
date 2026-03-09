"""Mean-reversion prediction: bets against recent price extremes."""

from __future__ import annotations

from starter_challenge.tracker import TrackerBase


class MeanReversionTracker(TrackerBase):
    """Predicts reversal when price deviates from recent average."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _extract_prices(self._get_data(subject))
        if len(prices) < 3:
            return {"value": 0.0}

        lookback = min(20, len(prices))
        window = prices[-lookback:]
        mean_price = sum(window) / len(window)
        current = window[-1]

        if mean_price == 0:
            return {"value": 0.0}

        # Deviation from mean — positive deviation → bearish (expect reversion)
        deviation = (current - mean_price) / mean_price
        value = max(-1.0, min(1.0, -deviation * 15.0))
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
