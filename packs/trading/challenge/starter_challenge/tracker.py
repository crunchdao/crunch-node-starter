"""Base tracker for trading signal competitions.

Models receive multi-timeframe candle data via ``tick()`` and must return
a directional signal in [-1, 1] from ``predict()``.

The ``predict()`` return value must match ``InferenceOutput``::

    {"signal": 0.5}   # long with moderate conviction
    {"signal": -1.0}  # max-conviction short
    {"signal": 0.0}   # flat / no position
"""

from __future__ import annotations

from typing import Any


class TrackerBase:
    """Base class for trading signal models.

    Subclass this and implement ``predict()`` to compete.
    Use ``tick()`` to maintain internal state (indicators, history, etc.).
    """

    def __init__(self) -> None:
        self._latest_data_by_subject: dict[str, dict[str, Any]] = {}

    def tick(self, data: dict[str, Any]) -> None:
        """Receive latest market data. Override to maintain state.

        Args:
            data: Feed data dict (shape matches ``RawInput`` — includes
                  candles_1m, candles_5m, candles_15m, candles_1h).
        """
        subject_key = (
            data.get("symbol", "_default") if isinstance(data, dict) else "_default"
        )
        self._latest_data_by_subject[subject_key] = data

    def _get_data(self, subject: str) -> dict[str, Any] | None:
        """Return the latest tick data for *subject*."""
        return self._latest_data_by_subject.get(
            subject,
            self._latest_data_by_subject.get("_default"),
        )

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict[str, Any]:
        """Return a trading signal for the given asset.

        Args:
            subject: Asset pair (e.g. "BTCUSDT", "ETHUSDT").
            resolve_horizon_seconds: Seconds until ground truth resolution.
            step_seconds: Time granularity within the horizon.

        Returns:
            Dict with ``{"signal": float}`` where signal is in [-1, 1].
        """
        raise NotImplementedError("Implement predict() in your model")
