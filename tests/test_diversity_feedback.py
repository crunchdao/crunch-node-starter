"""Tests for the model diversity feedback endpoint."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from crunch_node.entities.prediction import SnapshotRecord
from crunch_node.workers.report_worker import get_model_diversity


class InMemorySnapshotRepository:
    def __init__(self, snapshots=None):
        self._snapshots = snapshots or []

    def find(self, model_id=None, since=None, until=None, limit=100):
        result = list(self._snapshots)
        if model_id:
            result = [s for s in result if s.model_id == model_id]
        result.sort(key=lambda s: s.period_end, reverse=True)
        return result[:limit]


class InMemoryLeaderboardRepository:
    def __init__(self, entries=None):
        self._latest = {"entries": entries or []} if entries else None

    def get_latest(self):
        return self._latest


NOW = datetime.now(UTC)


class TestDiversityEndpoint(unittest.TestCase):
    def test_returns_diversity_metrics(self):
        snap = SnapshotRecord(
            id="snap1",
            model_id="m1",
            period_start=NOW - timedelta(hours=1),
            period_end=NOW,
            prediction_count=10,
            result_summary={
                "value": 0.5,
                "ic": 0.035,
                "model_correlation": 0.25,
                "ensemble_correlation": 0.6,
                "contribution": 0.02,
                "fnc": 0.03,
            },
        )
        snap_repo = InMemorySnapshotRepository([snap])
        lb_repo = InMemoryLeaderboardRepository(
            [
                {"model_id": "m1", "rank": 3},
            ]
        )

        result = get_model_diversity("m1", snap_repo, lb_repo)

        self.assertEqual(result["model_id"], "m1")
        self.assertEqual(result["rank"], 3)
        self.assertAlmostEqual(result["diversity_score"], 0.75)
        self.assertEqual(result["metrics"]["ic"], 0.035)
        self.assertEqual(result["metrics"]["model_correlation"], 0.25)
        self.assertEqual(result["metrics"]["contribution"], 0.02)
        self.assertIsInstance(result["guidance"], list)

    def test_high_correlation_guidance(self):
        snap = SnapshotRecord(
            id="snap1",
            model_id="m1",
            period_start=NOW - timedelta(hours=1),
            period_end=NOW,
            prediction_count=10,
            result_summary={
                "model_correlation": 0.85,
                "ensemble_correlation": 0.95,
                "contribution": -0.01,
            },
        )
        snap_repo = InMemorySnapshotRepository([snap])
        lb_repo = InMemoryLeaderboardRepository()

        result = get_model_diversity("m1", snap_repo, lb_repo)

        # Should have warnings about high correlation and negative contribution
        guidance_text = " ".join(result["guidance"])
        self.assertIn("correlation", guidance_text.lower())
        self.assertIn("negative contribution", guidance_text.lower())

    def test_unique_model_positive_guidance(self):
        snap = SnapshotRecord(
            id="snap1",
            model_id="m1",
            period_start=NOW - timedelta(hours=1),
            period_end=NOW,
            prediction_count=10,
            result_summary={
                "ic": 0.05,
                "model_correlation": 0.15,
                "contribution": 0.03,
            },
        )
        snap_repo = InMemorySnapshotRepository([snap])
        lb_repo = InMemoryLeaderboardRepository()

        result = get_model_diversity("m1", snap_repo, lb_repo)

        guidance_text = " ".join(result["guidance"])
        self.assertIn("unique alpha", guidance_text.lower())

    def test_404_for_unknown_model(self):
        snap_repo = InMemorySnapshotRepository([])
        lb_repo = InMemoryLeaderboardRepository()

        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            get_model_diversity("nonexistent", snap_repo, lb_repo)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_diversity_score_zero_for_perfect_correlation(self):
        snap = SnapshotRecord(
            id="snap1",
            model_id="m1",
            period_start=NOW - timedelta(hours=1),
            period_end=NOW,
            prediction_count=5,
            result_summary={"model_correlation": 1.0},
        )
        result = get_model_diversity(
            "m1",
            InMemorySnapshotRepository([snap]),
            InMemoryLeaderboardRepository(),
        )
        self.assertAlmostEqual(result["diversity_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
