from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from crunch_node.entities.prediction import SnapshotRecord
from crunch_node.services.score import ScoreService


class TestTradingScoring:
    def _make_service(self, snapshots=None):
        strategy = MagicMock()
        strategy.produce_snapshots = MagicMock(return_value=snapshots or [])
        strategy.rollback = MagicMock()
        return ScoreService(scoring_strategy=strategy)

    def test_strategy_called_on_score_cycle(self):
        snap = SnapshotRecord(
            id="SNAP_m1_test",
            model_id="m1",
            period_start=datetime.now(UTC),
            period_end=datetime.now(UTC),
            prediction_count=1,
            result_summary={"net_pnl": 0.01},
        )

        service = self._make_service(snapshots=[snap])
        result = service.score_and_snapshot()

        assert result is True
        service.scoring_strategy.produce_snapshots.assert_called_once()

    def test_no_snapshots_returns_false(self):
        service = self._make_service(snapshots=[])
        result = service.score_and_snapshot()
        assert result is False

    def test_empty_snapshots_returns_false(self):
        service = self._make_service(snapshots=[])
        result = service.score_and_snapshot()

        assert result is False
        service.scoring_strategy.produce_snapshots.assert_called_once()
