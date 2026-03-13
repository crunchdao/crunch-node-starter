"""Tests for multi-metric scoring in PredictionScorer._write_snapshots."""

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
from crunch_node.services.prediction_scorer import PredictionScorer


class InMemoryRepo:
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


class TestMultiMetricSnapshots(unittest.TestCase):
    def _make_scorer(self, metrics=None):
        config = CrunchConfig(
            metrics=metrics
            if metrics is not None
            else ["ic", "hit_rate", "max_drawdown"],
        )

        now = datetime.now(UTC)

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

        pred_repo = InMemoryRepo(predictions)
        score_repo = InMemoryRepo()
        snapshot_repo = InMemoryRepo()

        scorer = PredictionScorer(
            scoring_function=lambda p, g: {"value": 0.0},
            input_repository=InMemoryRepo([input1]),
            prediction_repository=pred_repo,
            score_repository=score_repo,
            snapshot_repository=snapshot_repo,
            config=config,
        )

        return scorer, scores, snapshot_repo

    def test_snapshots_contain_metric_keys(self):
        scorer, scores, snapshot_repo = self._make_scorer(
            metrics=["ic", "hit_rate", "max_drawdown"]
        )

        now = datetime.now(UTC)
        scorer._write_snapshots(scores, now)

        snapshots = snapshot_repo.find()
        self.assertEqual(len(snapshots), 1)

        summary = snapshots[0].result_summary
        self.assertIn("ic", summary)
        self.assertIn("hit_rate", summary)
        self.assertIn("max_drawdown", summary)
        self.assertIn("value", summary)

    def test_empty_metrics_list_no_enrichment(self):
        scorer, scores, snapshot_repo = self._make_scorer(metrics=[])

        now = datetime.now(UTC)
        scorer._write_snapshots(scores, now)

        snapshots = snapshot_repo.find()
        self.assertEqual(len(snapshots), 1)

        summary = snapshots[0].result_summary
        self.assertIn("value", summary)
        self.assertNotIn("ic", summary)

    def test_metrics_are_float_values(self):
        scorer, scores, snapshot_repo = self._make_scorer(
            metrics=["ic", "hit_rate", "turnover"]
        )

        now = datetime.now(UTC)
        scorer._write_snapshots(scores, now)

        snapshots = snapshot_repo.find()
        summary = snapshots[0].result_summary

        for key in ["ic", "hit_rate", "turnover"]:
            self.assertIsInstance(summary[key], float, f"{key} should be float")

    def test_ic_positive_for_aligned_predictions(self):
        scorer, scores, snapshot_repo = self._make_scorer(metrics=["ic"])

        now = datetime.now(UTC)
        scorer._write_snapshots(scores, now)

        snapshots = snapshot_repo.find()
        ic = snapshots[0].result_summary["ic"]
        self.assertGreater(ic, 0.0)

    def test_hit_rate_all_positive(self):
        scorer, scores, snapshot_repo = self._make_scorer(metrics=["hit_rate"])

        now = datetime.now(UTC)
        scorer._write_snapshots(scores, now)

        snapshots = snapshot_repo.find()
        hit_rate = snapshots[0].result_summary["hit_rate"]
        self.assertAlmostEqual(hit_rate, 1.0)


if __name__ == "__main__":
    unittest.main()
