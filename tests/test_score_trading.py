from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from crunch_node.entities.prediction import SnapshotRecord
from crunch_node.services.score import ScoreService


class TradingStrategy:
    def __init__(self, build_fn, snapshot_repository):
        self._build_fn = build_fn
        self.snapshot_repository = snapshot_repository

    def produce_snapshots(self, now):
        snapshots = self._build_fn(now)
        if snapshots:
            for snap in snapshots:
                self.snapshot_repository.save(snap)
        return snapshots or []

    def rollback(self):
        pass


class TestTradingScoring:
    def _make_service(self, build_snapshots_fn=None):
        snapshot_repo = MagicMock()
        if build_snapshots_fn is None:
            strategy = MagicMock()
            strategy.produce_snapshots = MagicMock(return_value=[])
            strategy.rollback = MagicMock()
        else:
            strategy = TradingStrategy(build_snapshots_fn, snapshot_repo)
        return ScoreService(scoring_strategy=strategy), snapshot_repo

    def test_build_snapshots_fn_called_on_score_cycle(self):
        snap = SnapshotRecord(
            id="SNAP_m1_test",
            model_id="m1",
            period_start=datetime.now(UTC),
            period_end=datetime.now(UTC),
            prediction_count=1,
            result_summary={"net_pnl": 0.01},
        )
        build_fn = MagicMock(return_value=[snap])

        service, snapshot_repo = self._make_service(build_snapshots_fn=build_fn)
        result = service.score_and_snapshot()

        assert result is True
        build_fn.assert_called_once()
        snapshot_repo.save.assert_called_once()

    def test_no_build_snapshots_fn_falls_through_to_prediction_scoring(self):
        service, _ = self._make_service(build_snapshots_fn=None)
        result = service.score_and_snapshot()
        assert result is False

    def test_build_snapshots_fn_empty_returns_false(self):
        build_fn = MagicMock(return_value=[])

        service, _ = self._make_service(build_snapshots_fn=build_fn)
        result = service.score_and_snapshot()

        assert result is False
        build_fn.assert_called_once()
