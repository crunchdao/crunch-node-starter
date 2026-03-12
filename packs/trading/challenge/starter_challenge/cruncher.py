"""Base tracker for trading competitions.

Models receive multi-timeframe candle data via ``feed_update()`` and must return
a buy/sell order from ``predict()``.

The ``predict()`` return value must match ``InferenceOutput``::

    {"action": "buy", "amount": 100}   # buy 100 units
    {"action": "sell", "amount": 50}   # sell 50 units
    {"action": "buy", "amount": 0}     # no-op
"""

from __future__ import annotations

from typing import Any


class ModelBaseClass:
    """Base class for trading models.

    Subclass this and implement ``predict()`` to compete.
    Use ``feed_update()`` to maintain internal state (indicators, history, etc.).
    """

    def __init__(self) -> None:
        self._latest_data_by_subject: dict[str, dict[str, Any]] = {}

    def tick(self, data: dict[str, Any]) -> None:
        """Called by the model runner on each feed update. Delegates to ``feed_update``."""
        self.feed_update(data)

    def feed_update(self, data: dict[str, Any]) -> None:
        """Receive latest market data. Override to maintain state."""
        subject_key = (
            data.get("symbol", "_default") if isinstance(data, dict) else "_default"
        )
        self._latest_data_by_subject[subject_key] = data

    def _get_data(self, subject: str) -> dict[str, Any] | None:
        """Return the latest feed data for *subject*."""
        return self._latest_data_by_subject.get(
            subject,
            self._latest_data_by_subject.get("_default"),
        )

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict[str, Any]:
        """Return a trading order for the given asset.

        Args:
            subject: Asset name (e.g. "BTC", "ETH").
            resolve_horizon_seconds: Seconds until ground truth resolution.
            step_seconds: Time granularity within the horizon.

        Returns:
            Dict with ``{"action": "buy"|"sell", "amount": float}``.
        """
        raise NotImplementedError("Implement predict() in your model")
