"""Base tracker for tournament-style competitions.

Models receive feature data via ``tick()`` and must return a prediction
value from ``predict()``. Predictions are ranked by IC (correlation with
the target), not absolute accuracy.

The ``predict()`` return value must match ``InferenceOutput``::

    {"prediction": 0.42}   # higher = more bullish
    {"prediction": -0.15}  # lower = more bearish
"""

from __future__ import annotations

from typing import Any


class TrackerBase:
    """Base class for tournament prediction models.

    Subclass this and implement ``predict()`` to compete.
    Use ``tick()`` to receive feature data and maintain state.
    """

    def __init__(self) -> None:
        self._latest_data_by_subject: dict[str, dict[str, Any]] = {}

    def tick(self, data: dict[str, Any]) -> None:
        """Receive latest feature data. Override to maintain state.

        Args:
            data: Feed data dict (shape matches ``RawInput`` — includes
                  features dict and round_id).
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
        """Return a prediction for the given scope.

        Args:
            subject: Asset or topic being predicted (e.g. "BTC").
            resolve_horizon_seconds: Seconds until ground truth resolution.
            step_seconds: Time granularity within the horizon.

        Returns:
            Dict with ``{"prediction": float}``.
        """
        raise NotImplementedError("Implement predict() in your model")
