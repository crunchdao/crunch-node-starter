"""Base tracker for prediction competitions.

Models receive candle data via ``feed_update()`` and must return
a directional prediction from ``predict()``.

The ``predict()`` return value must match ``PredictionOutput``::

    {"value": 0.5}    # bullish with moderate conviction
    {"value": -1.0}   # max-conviction bearish
    {"value": 0.0}    # no prediction / flat
"""

from __future__ import annotations

from typing import Any


class TrackerBase:
    """Base class for prediction models.

    Subclass this and implement ``predict()`` to compete.
    Use ``feed_update()`` to maintain internal state (indicators, history, etc.).
    """

    def __init__(self) -> None:
        self._latest_data_by_subject: dict[str, dict[str, Any]] = {}

    def feed_update(self, data: dict[str, Any]) -> None:
        """Receive latest market data. Override to maintain state.

        Data is stored per-subject so multi-asset competitions work correctly.
        The subject key is read from ``data["symbol"]``; if missing the data
        is stored under the key ``"_default"``.

        Args:
            data: Feed data dict with shape::

                {
                    "symbol": "BTCUSDT",
                    "asof_ts": 1700000000,
                    "candles_1m": [
                        {"ts": ..., "open": ..., "high": ...,
                         "low": ..., "close": ..., "volume": ...},
                        ...
                    ]
                }
        """
        subject_key = (
            data.get("symbol", "_default") if isinstance(data, dict) else "_default"
        )
        self._latest_data_by_subject[subject_key] = data

    def _get_data(self, subject: str) -> dict[str, Any] | None:
        """Return the latest feed data for *subject*.

        Falls back to ``"_default"`` when no exact match exists (single-asset
        competitions typically don't set ``symbol`` in the data dict).
        """
        return self._latest_data_by_subject.get(
            subject,
            self._latest_data_by_subject.get("_default"),
        )

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict[str, Any]:
        """Return a prediction for the given scope.

        Args:
            subject: Asset being predicted (e.g. "BTCUSDT").
            resolve_horizon_seconds: How far ahead ground truth is resolved (seconds).
            step_seconds: Time step between predictions (seconds).

        Returns:
            Dict matching ``PredictionOutput`` fields.
            Expects ``{"value": float}`` where positive means bullish
            and negative means bearish, magnitude = conviction.
        """
        raise NotImplementedError("Implement predict() in your model")
