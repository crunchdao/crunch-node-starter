"""Tests for ScoreService orchestrator."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from crunch_node.services.score import ScoreService


class TestScoreOrchestrator(unittest.TestCase):
    def test_calls_full_pipeline(self):
        strategy = MagicMock()
        strategy.produce_snapshots.return_value = [MagicMock()]
        ensemble = MagicMock()
        ensemble.compute_ensembles.return_value = []
        leaderboard = MagicMock()
        merkle = MagicMock()
        checkpoint = MagicMock()

        service = ScoreService(
            scoring_strategy=strategy,
            ensemble_strategy=ensemble,
            leaderboard_service=leaderboard,
            merkle_service=merkle,
            checkpoint_service=checkpoint,
        )

        result = service.score_and_snapshot()

        self.assertTrue(result)
        strategy.produce_snapshots.assert_called_once()
        ensemble.compute_ensembles.assert_called_once()
        merkle.commit_cycle.assert_called_once()
        leaderboard.rebuild.assert_called_once()
        checkpoint.maybe_checkpoint.assert_called_once()

    def test_returns_false_when_no_snapshots(self):
        strategy = MagicMock()
        strategy.produce_snapshots.return_value = []

        service = ScoreService(scoring_strategy=strategy)
        result = service.score_and_snapshot()
        self.assertFalse(result)

    def test_skips_optional_components(self):
        strategy = MagicMock()
        strategy.produce_snapshots.return_value = [MagicMock()]

        service = ScoreService(scoring_strategy=strategy)
        result = service.score_and_snapshot()
        self.assertTrue(result)

    def test_merkle_failure_does_not_block_pipeline(self):
        strategy = MagicMock()
        strategy.produce_snapshots.return_value = [MagicMock()]
        merkle = MagicMock()
        merkle.commit_cycle.side_effect = RuntimeError("boom")
        leaderboard = MagicMock()

        service = ScoreService(
            scoring_strategy=strategy,
            merkle_service=merkle,
            leaderboard_service=leaderboard,
        )
        result = service.score_and_snapshot()
        self.assertTrue(result)
        leaderboard.rebuild.assert_called_once()


class TestScoreOrchestratorRunLoop(unittest.IsolatedAsyncioTestCase):
    async def test_rollback_on_exception(self):
        strategy = MagicMock()
        strategy.produce_snapshots.side_effect = RuntimeError("boom")

        service = ScoreService(scoring_strategy=strategy, score_interval_seconds=1)

        def stop_after_first_error(*args, **kwargs):
            service.stop_event.set()
            raise RuntimeError("boom")

        strategy.produce_snapshots.side_effect = stop_after_first_error

        with self.assertLogs(level="ERROR"):
            await service.run()

        strategy.rollback.assert_called_once()
