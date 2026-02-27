"""Breakout trading signal: enters on range expansion."""

from __future__ import annotations

from starter_challenge.tracker import TrackerBase


class BreakoutTracker(TrackerBase):
    """Detects when price breaks above/below recent range and signals accordingly."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _extract_prices(self._get_data(subject))
        if len(prices) < 3:
            return {"signal": 0.0}

        lookback = min(20, len(prices))
        window = prices[-lookback:]
        current = window[-1]

        # Exclude last candle from range calculation
        range_window = window[:-1]
        high = max(range_window)
        low = min(range_window)
        range_size = high - low

        if range_size < 1e-9:
            return {"signal": 0.0}

        # Signal strength based on how far price is beyond the range
        if current > high:
            breakout_pct = (current - high) / range_size
            signal = min(1.0, breakout_pct * 5.0)
        elif current < low:
            breakout_pct = (low - current) / range_size
            signal = max(-1.0, -breakout_pct * 5.0)
        else:
            signal = 0.0

        return {"signal": round(signal, 4)}


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
