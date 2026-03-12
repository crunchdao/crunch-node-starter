"""Linear combination: equally weights all features."""

from __future__ import annotations

from typing import Any

from starter_challenge.cruncher import ModelBaseClass


class LinearComboTracker(ModelBaseClass):
    """Simple equal-weight linear combination of all features."""

    def predict(self, features: dict[str, Any]) -> dict[str, Any]:
        feats = features.get("features", features)
        if isinstance(feats, dict):
            values = [v for v in feats.values() if isinstance(v, (int, float))]
        else:
            values = []

        pred = round(sum(values) / len(values), 6) if values else 0.0
        return {"prediction": pred}
