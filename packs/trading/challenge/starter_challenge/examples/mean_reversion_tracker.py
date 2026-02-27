"""Mean reversion trading signal: fades short-term overextension."""

from __future__ import annotations

from starter_challenge.tracker import TrackerBase


class MeanReversionTracker(TrackerBase):
    """Shorts overextensions, longs pullbacks — classic mean reversion."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        prices = _extract_prices(self._get_data(subject))
        if len(prices) < 3:
            return {"signal": 0.0}

        lookback = min(20, len(prices))
        window = prices[-lookback:]
        average = sum(window) / lookback
        deviation = (window[-1] - average) / max(abs(average), 1e-9)

        # Fade the deviation: overshoot → counter-signal
        signal = max(-1.0, min(1.0, -deviation * 15.0))
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
