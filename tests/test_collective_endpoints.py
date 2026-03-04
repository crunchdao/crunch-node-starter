"""Tests for collective intelligence endpoints: diversity, ensemble history, reward history."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from crunch_node.entities.prediction import SnapshotRecord
from crunch_node.workers.report_worker import (
    get_checkpoint_rewards,
    get_diversity_overview,
    get_ensemble_history,
)

NOW = datetime.now(UTC)


class InMemorySnapshotRepository:
    def __init__(self, snapshots=None):
        self._snapshots = snapshots or []

    def find(self, model_id=None, since=None, until=None, limit=500):
        result = list(self._snapshots)
        if model_id:
            result = [s for s in result if s.model_id == model_id]
        if since:
            result = [s for s in result if s.period_end >= since]
        if until:
            result = [s for s in result if s.period_end <= until]
        result.sort(key=lambda s: s.period_end, reverse=True)
        return result[:limit]


class InMemoryCheckpointRepository:
    def __init__(self, checkpoints=None):
        self._checkpoints = checkpoints or []

    def find(self, status=None, limit=20):
        return self._checkpoints[:limit]


class TestDiversityOverview(unittest.TestCase):
    def test_returns_diversity_for_all_models(self):
        snaps = [
            SnapshotRecord(
                id="s1",
                model_id="m1",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=10,
                result_summary={
                    "ic": 0.03,
                    "model_correlation": 0.2,
                    "contribution": 0.05,
                },
            ),
            SnapshotRecord(
                id="s2",
                model_id="m2",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=8,
                result_summary={
                    "ic": 0.05,
                    "model_correlation": 0.8,
                    "contribution": 0.01,
                },
            ),
        ]
        result = get_diversity_overview(InMemorySnapshotRepository(snaps))

        self.assertEqual(len(result), 2)
        # Sorted by diversity_score desc — m1 (corr=0.2 → div=0.8) before m2 (corr=0.8 → div=0.2)
        self.assertEqual(result[0]["model_id"], "m1")
        self.assertAlmostEqual(result[0]["diversity_score"], 0.8)
        self.assertEqual(result[1]["model_id"], "m2")
        self.assertAlmostEqual(result[1]["diversity_score"], 0.2)

    def test_excludes_ensemble_models(self):
        snaps = [
            SnapshotRecord(
                id="s1",
                model_id="m1",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=10,
                result_summary={"model_correlation": 0.3},
            ),
            SnapshotRecord(
                id="s2",
                model_id="__ensemble_main__",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=10,
                result_summary={"model_correlation": 0.0},
            ),
        ]
        result = get_diversity_overview(InMemorySnapshotRepository(snaps))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model_id"], "m1")

    def test_uses_latest_snapshot_per_model(self):
        snaps = [
            SnapshotRecord(
                id="s1",
                model_id="m1",
                period_start=NOW - timedelta(hours=2),
                period_end=NOW - timedelta(hours=1),
                prediction_count=5,
                result_summary={"model_correlation": 0.9},
            ),
            SnapshotRecord(
                id="s2",
                model_id="m1",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=10,
                result_summary={"model_correlation": 0.3},
            ),
        ]
        result = get_diversity_overview(InMemorySnapshotRepository(snaps))
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["diversity_score"], 0.7)


class TestEnsembleHistory(unittest.TestCase):
    def test_returns_only_ensemble_snapshots(self):
        snaps = [
            SnapshotRecord(
                id="s1",
                model_id="__ensemble_main__",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=10,
                result_summary={"ic": 0.04, "ic_sharpe": 1.2},
            ),
            SnapshotRecord(
                id="s2",
                model_id="m1",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=5,
                result_summary={"ic": 0.03},
            ),
        ]
        result = get_ensemble_history(InMemorySnapshotRepository(snaps))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ensemble_name"], "main")
        self.assertEqual(result[0]["ic"], 0.04)

    def test_filters_by_ensemble_name(self):
        snaps = [
            SnapshotRecord(
                id="s1",
                model_id="__ensemble_main__",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=10,
                result_summary={"ic": 0.04},
            ),
            SnapshotRecord(
                id="s2",
                model_id="__ensemble_top5__",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=10,
                result_summary={"ic": 0.05},
            ),
        ]
        result = get_ensemble_history(
            InMemorySnapshotRepository(snaps), ensemble_name="top5"
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ensemble_name"], "top5")

    def test_sorted_by_period_end(self):
        snaps = [
            SnapshotRecord(
                id="s1",
                model_id="__ensemble_main__",
                period_start=NOW - timedelta(hours=2),
                period_end=NOW - timedelta(hours=1),
                prediction_count=5,
                result_summary={"ic": 0.02},
            ),
            SnapshotRecord(
                id="s2",
                model_id="__ensemble_main__",
                period_start=NOW - timedelta(hours=1),
                period_end=NOW,
                prediction_count=10,
                result_summary={"ic": 0.04},
            ),
        ]
        result = get_ensemble_history(InMemorySnapshotRepository(snaps))
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]["period_end"] < result[1]["period_end"])


class TestCheckpointRewards(unittest.TestCase):
    def _make_checkpoint(self, *, ranking, emission_rewards=None):
        from crunch_node.crunch_config import FRAC_64_MULTIPLIER

        cruncher_rewards = []
        if emission_rewards:
            cruncher_rewards = [
                {"cruncher_index": i, "reward_pct": int(pct / 100 * FRAC_64_MULTIPLIER)}
                for i, pct in enumerate(emission_rewards)
            ]

        class FakeCheckpoint:
            id = "CKP_1"
            period_start = NOW - timedelta(hours=1)
            period_end = NOW
            status = "PENDING"
            entries = (
                [
                    {
                        "crunch": "pubkey",
                        "cruncher_rewards": cruncher_rewards,
                        "compute_provider_rewards": [],
                        "data_provider_rewards": [],
                    }
                ]
                if cruncher_rewards
                else []
            )
            meta = {"ranking": ranking}
            created_at = NOW
            tx_hash = None
            submitted_at = None

        return FakeCheckpoint()

    def test_returns_reward_per_model(self):
        cp = self._make_checkpoint(
            ranking=[
                {
                    "model_id": "m1",
                    "model_name": "Alpha",
                    "rank": 1,
                    "result_summary": {"ic": 0.05},
                },
                {
                    "model_id": "m2",
                    "model_name": "Beta",
                    "rank": 2,
                    "result_summary": {"ic": 0.03},
                },
            ],
            emission_rewards=[60.0, 40.0],
        )
        result = get_checkpoint_rewards(InMemoryCheckpointRepository([cp]))

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["model_id"], "m1")
        self.assertAlmostEqual(result[0]["reward_pct"], 60.0, places=1)
        self.assertEqual(result[0]["rank"], 1)
        self.assertEqual(result[0]["ic"], 0.05)

    def test_excludes_ensemble_models(self):
        cp = self._make_checkpoint(
            ranking=[
                {"model_id": "m1", "rank": 1, "result_summary": {}},
                {"model_id": "__ensemble_main__", "rank": 2, "result_summary": {}},
            ],
        )
        result = get_checkpoint_rewards(InMemoryCheckpointRepository([cp]))
        self.assertEqual(len(result), 1)

    def test_filters_by_model_id(self):
        cp = self._make_checkpoint(
            ranking=[
                {"model_id": "m1", "rank": 1, "result_summary": {}},
                {"model_id": "m2", "rank": 2, "result_summary": {}},
            ],
        )
        result = get_checkpoint_rewards(
            InMemoryCheckpointRepository([cp]), model_id="m2"
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["model_id"], "m2")


class InMemoryCheckpointRepository:
    def __init__(self, checkpoints=None):
        self._checkpoints = checkpoints or []

    def find(self, status=None, limit=20):
        return self._checkpoints[:limit]


if __name__ == "__main__":
    unittest.main()
