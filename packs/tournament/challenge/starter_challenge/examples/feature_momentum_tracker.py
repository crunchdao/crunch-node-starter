"""Feature momentum: uses recent price momentum as prediction."""

from __future__ import annotations

from starter_challenge.tracker import TrackerBase


class FeatureMomentumTracker(TrackerBase):
    """Predicts based on recent candle momentum when features are sparse."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        data = self._get_data(subject)
        if data is None:
            return {"prediction": 0.0}

        # Try features first
        features = data.get("features", {})
        if features:
            # Average all feature values as a simple signal
            values = [v for v in features.values() if isinstance(v, (int, float))]
            if values:
                return {"prediction": round(sum(values) / len(values), 6)}

        # Fall back to candle momentum
        prices = _extract_prices(data)
        if len(prices) < 3:
            return {"prediction": 0.0}

        lookback = min(8, len(prices))
        window = prices[-lookback:]
        momentum = (window[-1] - window[0]) / max(abs(window[0]), 1e-9)
        return {"prediction": round(momentum, 6)}


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
