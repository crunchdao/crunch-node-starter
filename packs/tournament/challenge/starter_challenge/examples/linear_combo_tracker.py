"""Linear combination: equally weights all features."""

from __future__ import annotations

from starter_challenge.tracker import TrackerBase


class LinearComboTracker(TrackerBase):
    """Simple equal-weight linear combination of all features."""

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

        return {"prediction": round(sum(values) / len(values), 6)}
