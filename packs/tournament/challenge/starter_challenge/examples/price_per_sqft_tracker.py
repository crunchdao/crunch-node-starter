"""Predicts house price using a fixed price-per-square-foot estimate."""

from __future__ import annotations

from typing import Any

from starter_challenge.cruncher import ModelBaseClass

PRICE_PER_SQFT = 175.0


class PricePerSqftTracker(ModelBaseClass):
    """Multiplies living area by a fixed $/sqft constant."""

    def predict(self, features: dict[str, Any]) -> dict[str, Any]:
        feats = features.get("features", features)
        sqft = feats.get("living_area_sqft", 0.0)
        return {"prediction": round(sqft * PRICE_PER_SQFT, 2)}
