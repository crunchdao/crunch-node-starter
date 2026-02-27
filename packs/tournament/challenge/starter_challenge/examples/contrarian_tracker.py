"""Contrarian: inverts the feature signal, betting on mean reversion."""

from __future__ import annotations

from starter_challenge.tracker import TrackerBase


class ContrarianTracker(TrackerBase):
    """Inverts the average feature signal — contrarian bet."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        data = self._get_data(subject)
        if data is None:
            return {"prediction": 0.0}

        features = data.get("features", {})
        values = [v for v in features.values() if isinstance(v, (int, float))]
        if len(values) < 1:
            return {"prediction": 0.0}

        avg = sum(values) / len(values)
        return {"prediction": round(-avg, 6)}
