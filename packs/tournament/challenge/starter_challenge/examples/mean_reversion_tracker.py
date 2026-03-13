"""Predicts house price as the average of recent comparable sales."""

from __future__ import annotations

from typing import Any

from starter_challenge.cruncher import ModelBaseClass

BASELINE_PRICE = 320_000.0
SQFT_WEIGHT = 150.0


class MeanReversionTracker(ModelBaseClass):
    """Blends a baseline price with a square-footage adjustment."""

    def predict(self, features: dict[str, Any]) -> dict[str, Any]:
        feats = features.get("features", features)
        sqft = feats.get("living_area_sqft", 0.0)
        bedrooms = feats.get("bedrooms", 3)
        adjustment = (sqft - 1800) * SQFT_WEIGHT + (bedrooms - 3) * 15_000
        return {"prediction": round(BASELINE_PRICE + adjustment, 2)}
