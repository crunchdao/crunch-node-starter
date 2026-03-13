"""Always predicts a fixed median house price — the null-model baseline."""

from __future__ import annotations

from typing import Any

from starter_challenge.cruncher import ModelBaseClass

MEDIAN_PRICE = 350_000.0


class MedianPriceTracker(ModelBaseClass):
    """Returns a constant median price regardless of features."""

    def predict(self, features: dict[str, Any]) -> dict[str, Any]:
        return {"prediction": MEDIAN_PRICE}
