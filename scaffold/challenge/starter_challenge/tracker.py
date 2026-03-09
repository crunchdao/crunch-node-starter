from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TrackerBase:
    """Base class for participant models.

    Subclass this and implement ``_predict()`` (or override ``predict()``)
    to compete.  The ``feed_update()`` method receives market data on every
    feed update — use it to maintain internal state (indicators, history, etc.).

    The ``predict()`` wrapper logs inputs and outputs automatically.
    """

    def __init__(self) -> None:
        self._latest_data_by_subject: dict[str, dict[str, Any]] = {}
        self._model_name = type(self).__name__

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

        # Log feed summary
        if isinstance(data, dict):
            candles = data.get("candles_1m", [])
            last_close = candles[-1].get("close") if candles else None
            logger.info(
                "[%s] feed_update subject=%s candles=%d last_close=%s",
                self._model_name,
                subject_key,
                len(candles),
                last_close,
            )

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
            subject: Asset being predicted (e.g. "BTCUSDT", "ETHUSDT").
            resolve_horizon_seconds: How far ahead ground truth is resolved (seconds).
            step_seconds: Time step between predictions (seconds).

        Returns:
            Dict matching ``InferenceOutput`` fields.
            Default starter expects ``{"value": float}`` where positive
            means bullish and negative means bearish.
        """
        result = self._predict(subject, resolve_horizon_seconds, step_seconds)
        logger.info(
            "[%s] predict subject=%s horizon=%ds → %s",
            self._model_name,
            subject,
            resolve_horizon_seconds,
            result,
        )
        return result

    def _predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict[str, Any]:
        """Override this in your model. See ``predict()`` for docs."""
        raise NotImplementedError("Implement _predict() or predict() in your model")
