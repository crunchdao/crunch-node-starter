"""Mean reversion trading: buys dips, sells rips."""

from __future__ import annotations

from starter_challenge.cruncher import BaseModelClass


class MeanReversionTracker(BaseModelClass):
    """Buys when price dips below average, sells when it rises above."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _extract_prices(self._get_data(subject))
        if len(prices) < 3:
            return {"action": "buy", "amount": 0}

        lookback = min(20, len(prices))
        window = prices[-lookback:]
        average = sum(window) / lookback
        deviation = (window[-1] - average) / max(abs(average), 1e-9)

        size = round(abs(deviation) * 1000, 2)
        if deviation < 0:
            return {"action": "buy", "amount": size}
        else:
            return {"action": "sell", "amount": size}


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
