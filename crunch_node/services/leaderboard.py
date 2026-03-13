"""Leaderboard service: aggregate snapshots into ranked leaderboard entries."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from crunch_node.crunch_config import Aggregation
from crunch_node.entities.prediction import SnapshotRecord

logger = logging.getLogger(__name__)


class LeaderboardService:
    def __init__(
        self,
        snapshot_repository: Any,
        model_repository: Any,
        leaderboard_repository: Any,
        aggregation: Aggregation,
    ) -> None:
        self.snapshot_repository = snapshot_repository
        self.model_repository = model_repository
        self.leaderboard_repository = leaderboard_repository
        self.aggregation = aggregation

    def rebuild(self) -> None:
        models = self.model_repository.fetch_all()
        snapshots = self.snapshot_repository.find() if self.snapshot_repository else []

        aggregated = self._aggregate_from_snapshots(snapshots, models)
        ranked = self._rank(aggregated)

        self.leaderboard_repository.save(
            ranked,
            meta={"generated_by": "crunch_node.leaderboard_service"},
        )

    def _aggregate_from_snapshots(
        self, snapshots: list[SnapshotRecord], models: dict
    ) -> list[dict[str, Any]]:
        now = datetime.now(UTC)

        by_model: dict[str, list[SnapshotRecord]] = {}
        for snap in snapshots:
            by_model.setdefault(snap.model_id, []).append(snap)

        entries: list[dict[str, Any]] = []
        for model_id, model_snapshots in by_model.items():
            metrics: dict[str, float] = {}

            for window_name, window in self.aggregation.windows.items():
                cutoff = now - timedelta(hours=window.hours)
                window_snaps = [
                    s for s in model_snapshots if _ensure_utc(s.period_end) >= cutoff
                ]
                if window_snaps:
                    vals = [
                        float(s.result_summary.get(self.aggregation.value_field, 0))
                        for s in window_snaps
                    ]
                    metrics[window_name] = sum(vals) / len(vals)
                else:
                    metrics[window_name] = 0.0

            latest_snap = max(model_snapshots, key=lambda s: _ensure_utc(s.period_end))
            for key, value in latest_snap.result_summary.items():
                if key not in metrics:
                    try:
                        metrics[key] = float(value)
                    except (ValueError, TypeError):
                        pass

            model = models.get(model_id)
            entry: dict[str, Any] = {
                "model_id": model_id,
                "score": {
                    "metrics": metrics,
                    "ranking": {
                        "key": self.aggregation.ranking_key,
                        "value": metrics.get(self.aggregation.ranking_key, 0.0),
                        "direction": self.aggregation.ranking_direction,
                    },
                },
            }
            if model:
                entry["model_name"] = model.name
                entry["cruncher_name"] = model.player_name
            entries.append(entry)

        return entries

    def _rank(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        key = self.aggregation.ranking_key
        reverse = self.aggregation.ranking_direction == "desc"

        def sort_key(e: dict[str, Any]) -> float:
            score = e.get("score")
            if not isinstance(score, dict):
                return float("-inf")
            try:
                return float((score.get("metrics") or {}).get(key, 0.0))
            except Exception:
                return float("-inf")

        ranked = sorted(entries, key=sort_key, reverse=reverse)
        for idx, entry in enumerate(ranked, start=1):
            entry["rank"] = idx
        return ranked


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (assume UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
