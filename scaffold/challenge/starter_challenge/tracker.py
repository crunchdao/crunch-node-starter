from __future__ import annotations

from typing import Any


class TrackerBase:
    """Base class for participant models.

    Subclass this and implement ``predict()`` to compete.
    The ``feed_update()`` method receives market data on every feed update —
    use it to maintain internal state (indicators, history, etc.).

    The ``predict()`` signature must match the coordinator's
    ``CallMethodConfig``. The default expects::

        predict(subject="BTC", resolve_horizon_seconds=60, step_seconds=15)

    and must return a dict matching ``InferenceOutput`` (e.g. ``{"value": 0.5}``).
    """

    def __init__(self) -> None:
        self._latest_data_by_subject: dict[str, dict[str, Any]] = {}

    def tick(self, data: dict[str, Any]) -> None:
        """Called by the model runner on each feed update. Delegates to ``feed_update``."""
        self.feed_update(data)

    def feed_update(self, data: dict[str, Any]) -> None:
        """Receive latest market data. Override to maintain state.

        Data is stored per-subject so multi-asset competitions work correctly.
        The subject key is read from ``data["symbol"]``; if missing the data
        is stored under the key ``"_default"``.

        Args:
            data: Feed data dict (shape matches ``RawInput``).
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
            subject: Asset being predicted (e.g. "BTC", "ETHUSDT").
            resolve_horizon_seconds: How far ahead ground truth is resolved (seconds).
            step_seconds: Time step between predictions (seconds).

        Returns:
            Dict matching ``InferenceOutput`` fields.
            Default starter expects ``{"value": float}`` where positive
            means bullish and negative means bearish.
        """
        raise NotImplementedError("Implement predict() in your model")
