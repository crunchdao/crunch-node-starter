from __future__ import annotations

from math import sqrt

from starter_challenge.tracker import TrackerBase


class VolatilityRegimeTracker(TrackerBase):
    """Dampens directional signal when short-term volatility spikes."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _extract_prices(self._get_data(subject))
        returns = _returns(prices)
        if len(returns) < 3:
            return {"value": 0.0}

        split = max(1, len(returns) // 2)
        baseline_vol = _volatility(returns[:split])
        recent_vol = _volatility(returns[split:])

        momentum = sum(returns[-3:]) / min(3, len(returns))
        volatility_ratio = recent_vol / max(baseline_vol, 1e-6)
        dampening = 1.0 + max(0.0, volatility_ratio - 1.0)

        return {"value": momentum / dampening}


def _extract_prices(latest_data):
    if isinstance(latest_data, dict) and isinstance(
        latest_data.get("candles_1m"), list
    ):
        return _closes(latest_data["candles_1m"])
    return []


def _returns(prices):
    if len(prices) < 2:
        return []

    output = []
    for idx in range(1, len(prices)):
        prev = prices[idx - 1]
        cur = prices[idx]
        if prev == 0:
            continue
        output.append((cur - prev) / prev)
    return output


def _volatility(values):
    if not values:
        return 0.0
    return sqrt(sum(value * value for value in values) / len(values))


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
