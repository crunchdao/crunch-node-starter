"""Contrarian: inverts the feature signal, betting on mean reversion."""

from __future__ import annotations

from typing import Any

from starter_challenge.tracker import TrackerBase


class ContrarianTracker(TrackerBase):
    """Inverts the average feature signal — contrarian bet."""

    def predict(self, features: dict[str, Any]) -> dict[str, Any]:
        feats = features.get("features", features)
        if isinstance(feats, dict):
            values = [v for v in feats.values() if isinstance(v, (int, float))]
        else:
            values = []

        avg = sum(values) / len(values) if values else 0.0
        return {"prediction": round(-avg, 6)}
