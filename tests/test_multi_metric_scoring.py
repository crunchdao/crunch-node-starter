"""End-to-end tests for multi-metric scoring in the score pipeline."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    PredictionStatus,
    ScoreRecord,
)
from crunch_node.services.score import ScoreService


class InMemoryRepo:
    """Minimal in-memory repository for testing."""

    def __init__(self, items=None):
        self._items = {
            getattr(i, "id", str(idx)): i for idx, i in enumerate(items or [])
        }

    def get(self, item_id):
        return self._items.get(item_id)

    def save(self, item):
        self._items[item.id] = item

    def find(self, status=None, resolvable_before=None, **kwargs):
        items = list(self._items.values())
        if status is not None:
            items = [i for i in items if getattr(i, "status", None) == status]
        return items

    def fetch_all(self):
        return dict(self._items)

    def rollback(self):
        pass


class InMemoryLeaderboardRepo:
    def __init__(self):
        self._latest = None

    def save(self, entries, meta=None):
        self._latest = {"entries": entries, "meta": meta}

    def get_latest(self):
        return self._latest

    def rollback(self):
        pass


class TestMultiMetricSnapshots(unittest.TestCase):
    """Test that _write_snapshots produces enriched result_summary with metrics."""

    def _make_service(self, metrics=None):
        contract = CrunchConfig(
            metrics=metrics
            if metrics is not None
            else ["ic", "hit_rate", "max_drawdown"],
        )

        now = datetime.now(UTC)

        # Create scored predictions
        input1 = InputRecord(
            id="inp1",
            raw_data={},
            received_at=now - timedelta(minutes=5),
        )

        predictions = [
            PredictionRecord(
                id=f"pred_m1_{i}",
                input_id="inp1",
                model_id="model_a",
                prediction_config_id="cfg1",
                scope_key="BTC-60",
                scope={"subject": "BTC"},
                status=PredictionStatus.SCORED,
                exec_time_ms=10.0,
                inference_output={"value": float(i + 1)},
                performed_at=now - timedelta(seconds=60 * (5 - i)),
                resolvable_at=now - timedelta(seconds=30),
            )
            for i in range(5)
        ]

        scores = [
            ScoreRecord(
                id=f"scr_m1_{i}",
                prediction_id=f"pred_m1_{i}",
                result={"value": 0.1 * (i + 1), "actual_return": 0.01 * (i + 1)},
                success=True,
                scored_at=now - timedelta(seconds=60 * (5 - i)),
            )
            for i in range(5)
        ]

        input_repo = InMemoryRepo([input1])
        pred_repo = InMemoryRepo(predictions)
        score_repo = InMemoryRepo()
        snapshot_repo = InMemoryRepo()
        model_repo = InMemoryRepo()
        leaderboard_repo = InMemoryLeaderboardRepo()

        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=lambda p, g: {"value": 0.0},
            input_repository=input_repo,
            prediction_repository=pred_repo,
            score_repository=score_repo,
            snapshot_repository=snapshot_repo,
            model_repository=model_repo,
            leaderboard_repository=leaderboard_repo,
            contract=contract,
        )

        return service, scores, snapshot_repo

    def test_snapshots_contain_metric_keys(self):
        service, scores, snapshot_repo = self._make_service(
            metrics=["ic", "hit_rate", "max_drawdown"]
        )

        now = datetime.now(UTC)
        service._write_snapshots(scores, now)

        snapshots = snapshot_repo.find()
        self.assertEqual(len(snapshots), 1)

        summary = snapshots[0].result_summary
        self.assertIn("ic", summary)
        self.assertIn("hit_rate", summary)
        self.assertIn("max_drawdown", summary)
        # Baseline aggregation key should also be present
        self.assertIn("value", summary)

    def test_empty_metrics_list_no_enrichment(self):
        service, scores, snapshot_repo = self._make_service(metrics=[])

        now = datetime.now(UTC)
        service._write_snapshots(scores, now)

        snapshots = snapshot_repo.find()
        self.assertEqual(len(snapshots), 1)

        summary = snapshots[0].result_summary
        # Should have baseline 'value' but no metric keys
        self.assertIn("value", summary)
        self.assertNotIn("ic", summary)

    def test_metrics_are_float_values(self):
        service, scores, snapshot_repo = self._make_service(
            metrics=["ic", "hit_rate", "turnover"]
        )

        now = datetime.now(UTC)
        service._write_snapshots(scores, now)

        snapshots = snapshot_repo.find()
        summary = snapshots[0].result_summary

        for key in ["ic", "hit_rate", "turnover"]:
            self.assertIsInstance(summary[key], float, f"{key} should be float")

    def test_ic_positive_for_aligned_predictions(self):
        """When predictions increase with actual returns, IC should be positive."""
        service, scores, snapshot_repo = self._make_service(metrics=["ic"])

        now = datetime.now(UTC)
        service._write_snapshots(scores, now)

        snapshots = snapshot_repo.find()
        ic = snapshots[0].result_summary["ic"]
        self.assertGreater(ic, 0.0)

    def test_hit_rate_all_positive(self):
        """All predictions and returns positive → 100% hit rate."""
        service, scores, snapshot_repo = self._make_service(metrics=["hit_rate"])

        now = datetime.now(UTC)
        service._write_snapshots(scores, now)

        snapshots = snapshot_repo.find()
        hit_rate = snapshots[0].result_summary["hit_rate"]
        self.assertAlmostEqual(hit_rate, 1.0)


class TestEnsembleInScorePipeline(unittest.TestCase):
    """Test that ensemble virtual models are created and scored in the pipeline."""

    def test_ensemble_creates_virtual_model_snapshot(self):
        from crunch_node.crunch_config import EnsembleConfig
        from crunch_node.services.ensemble import equal_weight

        contract = CrunchConfig(
            metrics=["ic", "hit_rate"],
            ensembles=[EnsembleConfig(name="main", strategy=equal_weight)],
        )

        now = datetime.now(UTC)

        input1 = InputRecord(
            id="inp1",
            raw_data={},
            received_at=now - timedelta(minutes=5),
        )

        # Two models
        predictions = []
        scores = []
        for model_idx, model_id in enumerate(["model_a", "model_b"]):
            for i in range(3):
                pred_id = f"pred_{model_id}_{i}"
                predictions.append(
                    PredictionRecord(
                        id=pred_id,
                        input_id="inp1",
                        model_id=model_id,
                        prediction_config_id="cfg1",
                        scope_key="BTC-60",
                        scope={"subject": "BTC"},
                        status=PredictionStatus.SCORED,
                        exec_time_ms=10.0,
                        inference_output={"value": float(i + 1 + model_idx)},
                        performed_at=now - timedelta(seconds=60 * (3 - i)),
                        resolvable_at=now - timedelta(seconds=30),
                    )
                )
                scores.append(
                    ScoreRecord(
                        id=f"scr_{model_id}_{i}",
                        prediction_id=pred_id,
                        result={
                            "value": 0.1 * (i + 1),
                            "actual_return": 0.01 * (i + 1),
                        },
                        success=True,
                        scored_at=now - timedelta(seconds=60 * (3 - i)),
                    )
                )

        input_repo = InMemoryRepo([input1])
        pred_repo = InMemoryRepo(predictions)
        score_repo = InMemoryRepo()
        snapshot_repo = InMemoryRepo()
        model_repo = InMemoryRepo()
        leaderboard_repo = InMemoryLeaderboardRepo()

        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=lambda p, g: {"value": getattr(p, "value", 0) * 0.1},
            input_repository=input_repo,
            prediction_repository=pred_repo,
            score_repository=score_repo,
            snapshot_repository=snapshot_repo,
            model_repository=model_repo,
            leaderboard_repository=leaderboard_repo,
            contract=contract,
        )

        # Run snapshots + ensembles
        service._write_snapshots(scores, now)
        service._compute_ensembles(scores, now)

        # Should have snapshots for model_a, model_b, and __ensemble_main__
        snapshots = snapshot_repo.find()
        model_ids = {s.model_id for s in snapshots}

        self.assertIn("model_a", model_ids)
        self.assertIn("model_b", model_ids)
        self.assertIn("__ensemble_main__", model_ids)

        # Ensemble snapshot should have metrics
        ens_snap = [s for s in snapshots if s.model_id == "__ensemble_main__"][0]
        self.assertIn("value", ens_snap.result_summary)

    def test_no_ensemble_when_empty_config(self):
        contract = CrunchConfig(metrics=["ic"], ensembles=[])

        now = datetime.now(UTC)
        predictions = [
            PredictionRecord(
                id="pred1",
                input_id="inp1",
                model_id="m1",
                prediction_config_id="cfg1",
                scope_key="BTC-60",
                scope={},
                status=PredictionStatus.SCORED,
                exec_time_ms=10.0,
                inference_output={"value": 1.0},
                performed_at=now,
            )
        ]
        scores = [
            ScoreRecord(
                id="scr1",
                prediction_id="pred1",
                result={"value": 0.1, "actual_return": 0.01},
                success=True,
                scored_at=now,
            )
        ]

        pred_repo = InMemoryRepo(predictions)
        snapshot_repo = InMemoryRepo()

        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=lambda p, g: {"value": 0.0},
            prediction_repository=pred_repo,
            snapshot_repository=snapshot_repo,
            model_repository=InMemoryRepo(),
            leaderboard_repository=InMemoryLeaderboardRepo(),
            contract=contract,
        )

        service._write_snapshots(scores, now)
        service._compute_ensembles(scores, now)

        snapshots = snapshot_repo.find()
        # Only real model snapshot, no ensemble
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].model_id, "m1")


if __name__ == "__main__":
    unittest.main()
