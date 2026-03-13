"""Breakout trading: buys when price breaks above range, sells on break below."""

from __future__ import annotations

from typing import Any

from starter_challenge.cruncher import ModelBaseClass


class BreakoutTracker(ModelBaseClass):
    """Detects when price breaks above/below recent range and trades accordingly."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict[str, str | float]:
        prices = _extract_prices(self._get_data(subject))
        if len(prices) < 3:
            return {"action": "buy", "amount": 0}

        lookback = min(20, len(prices))
        window = prices[-lookback:]
        current = window[-1]

        range_window = window[:-1]
        high = max(range_window)
        low = min(range_window)
        range_size = high - low

        if range_size < 1e-9:
            return {"action": "buy", "amount": 0}

        if current > high:
            breakout_pct = (current - high) / range_size
            size = round(min(1.0, breakout_pct * 5.0) * 1000, 2)
            return {"action": "buy", "amount": size}
        elif current < low:
            breakout_pct = (low - current) / range_size
            size = round(min(1.0, breakout_pct * 5.0) * 1000, 2)
            return {"action": "sell", "amount": size}
        else:
            return {"action": "buy", "amount": 0}


def _extract_prices(latest_data: dict[str, Any] | None) -> list[float]:
    if isinstance(latest_data, dict) and isinstance(
        latest_data.get("candles_1m"), list
    ):
        return _closes(latest_data["candles_1m"])
    return []


def _closes(candles: list[dict[str, Any]]) -> list[float]:
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
