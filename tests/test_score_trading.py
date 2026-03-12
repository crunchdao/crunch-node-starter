from __future__ import annotations

from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock

from crunch_node.entities.prediction import SnapshotRecord
from crunch_node.services.score import ScoreService


class TestTradingScoring:
    def _make_service(self, build_snapshots_fn=None):
        return ScoreService(
            checkpoint_interval_seconds=300,
            scoring_function=lambda p, g: MagicMock(model_dump=lambda: {"value": 0}),
            snapshot_repository=MagicMock(),
            model_repository=MagicMock(fetch_all=MagicMock(return_value={})),
            leaderboard_repository=MagicMock(),
            prediction_repository=MagicMock(find=MagicMock(return_value=[])),
            build_snapshots_fn=build_snapshots_fn,
        )

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

        service = self._make_service(build_snapshots_fn=build_fn)
        result = service.score_and_snapshot()

        assert result is True
        build_fn.assert_called_once()
        service.snapshot_repository.save.assert_called_once()

    def test_no_build_snapshots_fn_falls_through_to_prediction_scoring(self):
        service = self._make_service(build_snapshots_fn=None)
        result = service.score_and_snapshot()
        assert result is False

    def test_build_snapshots_fn_empty_returns_false(self):
        build_fn = MagicMock(return_value=[])

        service = self._make_service(build_snapshots_fn=build_fn)
        result = service.score_and_snapshot()

        assert result is False
        build_fn.assert_called_once()
