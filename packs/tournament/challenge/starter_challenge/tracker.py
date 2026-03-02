"""Base tracker for tournament-style competitions.

Models receive a single feature dict via ``predict()`` and return
a prediction dict. The model runner calls ``predict(features)``
where ``features`` is one sample's data decoded from JSON.

The ``predict()`` return value must match ``InferenceOutput``::

    {"prediction": 0.42}   # higher = more bullish
    {"prediction": -0.15}  # lower = more bearish
"""

from __future__ import annotations

from typing import Any


class TrackerBase:
    """Base class for tournament prediction models.

    Subclass this and implement ``predict()`` to compete.
    The tournament engine calls ``predict()`` once per feature sample.
    """

    def predict(self, features: dict[str, Any]) -> dict[str, Any]:
        """Process a single feature sample and return a prediction.

        Args:
            features: Feature dict for one sample. Contains the fields
                defined by the competition's ``InferenceInput`` type.

        Returns:
            Dict matching ``InferenceOutput`` — at minimum
            ``{"prediction": float}``.
        """
        raise NotImplementedError("Implement predict() in your model")
