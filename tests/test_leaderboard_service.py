from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from crunch_node.crunch_config import Aggregation, AggregationWindow
from crunch_node.entities.model import Model
from crunch_node.entities.prediction import SnapshotRecord
from crunch_node.services.leaderboard import LeaderboardService


class MemSnapshotRepository:
    def __init__(self) -> None:
        self.snapshots: list[SnapshotRecord] = []

    def save(self, record: SnapshotRecord) -> None:
        self.snapshots.append(record)

    def find(self, **kwargs: Any) -> list[SnapshotRecord]:
        return list(self.snapshots)


class MemModelRepository:
    def __init__(self, models: dict[str, Model] | None = None) -> None:
        self.models = models or {}

    def fetch_all(self) -> dict[str, Model]:
        return self.models


class MemLeaderboardRepository:
    def __init__(self) -> None:
        self.latest: Any = None

    def save(self, entries: Any, meta: Any = None) -> None:
        self.latest = {"entries": entries, "meta": meta or {}}


def _make_model(model_id: str, name: str = "model", player: str = "player") -> Model:
    return Model(
        id=model_id,
        name=name,
        player_id=f"p-{model_id}",
        player_name=player,
        deployment_identifier=f"d-{model_id}",
    )


def _make_snapshot(
    model_id: str,
    period_end: datetime,
    result_summary: dict[str, Any] | None = None,
) -> SnapshotRecord:
    return SnapshotRecord(
        id=f"snap-{model_id}-{period_end.isoformat()}",
        model_id=model_id,
        period_start=period_end - timedelta(minutes=1),
        period_end=period_end,
        result_summary=result_summary or {"value": 1.0},
    )


class TestLeaderboardService(unittest.TestCase):
    def _build_service(
        self,
        snapshots: list[SnapshotRecord] | None = None,
        models: dict[str, Model] | None = None,
        aggregation: Aggregation | None = None,
    ) -> tuple[LeaderboardService, MemLeaderboardRepository]:
        snap_repo = MemSnapshotRepository()
        for s in (snapshots or []):
            snap_repo.save(s)
        model_repo = MemModelRepository(models or {})
        lb_repo = MemLeaderboardRepository()
        svc = LeaderboardService(
            snapshot_repository=snap_repo,
            model_repository=model_repo,
            leaderboard_repository=lb_repo,
            aggregation=aggregation or Aggregation(),
        )
        return svc, lb_repo

    def test_rebuild_empty_snapshots(self) -> None:
        svc, lb_repo = self._build_service()
        svc.rebuild()
        assert lb_repo.latest is not None
        assert lb_repo.latest["entries"] == []

    def test_rebuild_single_model(self) -> None:
        now = datetime.now(UTC)
        snap = _make_snapshot("m1", now, {"value": 5.0})
        model = _make_model("m1", name="alpha", player="alice")

        svc, lb_repo = self._build_service(
            snapshots=[snap], models={"m1": model}
        )
        svc.rebuild()

        entries = lb_repo.latest["entries"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["model_id"] == "m1"
        assert entry["model_name"] == "alpha"
        assert entry["cruncher_name"] == "alice"
        assert entry["rank"] == 1

    def test_ranking_desc(self) -> None:
        now = datetime.now(UTC)
        snap_a = _make_snapshot("m1", now, {"value": 3.0})
        snap_b = _make_snapshot("m2", now, {"value": 7.0})

        svc, lb_repo = self._build_service(
            snapshots=[snap_a, snap_b],
            models={"m1": _make_model("m1"), "m2": _make_model("m2")},
        )
        svc.rebuild()

        entries = lb_repo.latest["entries"]
        assert entries[0]["model_id"] == "m2"
        assert entries[0]["rank"] == 1
        assert entries[1]["model_id"] == "m1"
        assert entries[1]["rank"] == 2

    def test_ranking_asc(self) -> None:
        now = datetime.now(UTC)
        snap_a = _make_snapshot("m1", now, {"value": 3.0})
        snap_b = _make_snapshot("m2", now, {"value": 7.0})

        agg = Aggregation(ranking_direction="asc")
        svc, lb_repo = self._build_service(
            snapshots=[snap_a, snap_b],
            models={"m1": _make_model("m1"), "m2": _make_model("m2")},
            aggregation=agg,
        )
        svc.rebuild()

        entries = lb_repo.latest["entries"]
        assert entries[0]["model_id"] == "m1"
        assert entries[0]["rank"] == 1

    def test_window_filtering(self) -> None:
        now = datetime.now(UTC)
        recent = _make_snapshot("m1", now - timedelta(hours=1), {"value": 10.0})
        old = _make_snapshot("m1", now - timedelta(hours=100), {"value": 100.0})

        agg = Aggregation(
            windows={"short": AggregationWindow(hours=24)},
            ranking_key="short",
        )
        svc, lb_repo = self._build_service(
            snapshots=[recent, old],
            models={"m1": _make_model("m1")},
            aggregation=agg,
        )
        svc.rebuild()

        entry = lb_repo.latest["entries"][0]
        assert entry["score"]["metrics"]["short"] == 10.0

    def test_custom_value_field(self) -> None:
        now = datetime.now(UTC)
        snap = _make_snapshot("m1", now, {"net_pnl": 42.0, "value": 1.0})

        agg = Aggregation(
            windows={"w": AggregationWindow(hours=24)},
            value_field="net_pnl",
            ranking_key="w",
        )
        svc, lb_repo = self._build_service(
            snapshots=[snap],
            models={"m1": _make_model("m1")},
            aggregation=agg,
        )
        svc.rebuild()

        entry = lb_repo.latest["entries"][0]
        assert entry["score"]["metrics"]["w"] == 42.0

    def test_extra_numeric_fields_from_latest_snapshot(self) -> None:
        now = datetime.now(UTC)
        snap = _make_snapshot("m1", now, {"value": 5.0, "drawdown": 0.3})

        svc, lb_repo = self._build_service(
            snapshots=[snap],
            models={"m1": _make_model("m1")},
        )
        svc.rebuild()

        metrics = lb_repo.latest["entries"][0]["score"]["metrics"]
        assert metrics["drawdown"] == 0.3

    def test_model_without_model_record(self) -> None:
        now = datetime.now(UTC)
        snap = _make_snapshot("unknown", now, {"value": 1.0})

        svc, lb_repo = self._build_service(snapshots=[snap], models={})
        svc.rebuild()

        entry = lb_repo.latest["entries"][0]
        assert entry["model_id"] == "unknown"
        assert "model_name" not in entry
        assert "cruncher_name" not in entry

    def test_naive_datetime_treated_as_utc(self) -> None:
        naive_dt = datetime(2025, 1, 1, 12, 0, 0)
        snap = _make_snapshot("m1", naive_dt, {"value": 1.0})

        agg = Aggregation(
            windows={"all": AggregationWindow(hours=999999)},
            ranking_key="all",
        )
        svc, lb_repo = self._build_service(
            snapshots=[snap],
            models={"m1": _make_model("m1")},
            aggregation=agg,
        )
        svc.rebuild()

        entries = lb_repo.latest["entries"]
        assert len(entries) == 1

    def test_meta_contains_generated_by(self) -> None:
        svc, lb_repo = self._build_service()
        svc.rebuild()
        assert lb_repo.latest["meta"]["generated_by"] == "crunch_node.leaderboard_service"


if __name__ == "__main__":
    unittest.main()
