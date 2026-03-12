"""Momentum-based trading: buys when price is trending up, sells when down."""

from __future__ import annotations

from starter_challenge.cruncher import BaseClass


class MomentumTracker(BaseClass):
    """Outputs a buy/sell order based on recent price momentum."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _extract_prices(self._get_data(subject))
        if len(prices) < 3:
            return {"action": "buy", "amount": 0}

        lookback = min(8, len(prices))
        window = prices[-lookback:]
        momentum = (window[-1] - window[0]) / max(abs(window[0]), 1e-9)

        if momentum > 0:
            return {"action": "buy", "amount": round(abs(momentum) * 1000, 2)}
        else:
            return {"action": "sell", "amount": round(abs(momentum) * 1000, 2)}


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
