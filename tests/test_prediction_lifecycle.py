"""Integration test: full prediction lifecycle with shared in-memory repositories.

feed_reader → predict_service → [input_repo, prediction_repo] → score_service →
    [score_repo, snapshot_repo, leaderboard_repo] → checkpoint_service → [checkpoint_repo]

Covers: PENDING → RESOLVED → SCORED, snapshots, checkpoints with prize distribution.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from crunch_node.crunch_config import (
    FRAC_64_MULTIPLIER,
    CrunchConfig,
    default_build_emission,
)
from crunch_node.entities.model import Model
from crunch_node.entities.prediction import (
    CheckpointRecord,
    CheckpointStatus,
    InputRecord,
    PredictionRecord,
    PredictionStatus,
    ScoreRecord,
)
from crunch_node.services.realtime_predict import RealtimePredictService
from crunch_node.services.score import ScoreService
from crunch_node.workers.checkpoint_worker import CheckpointService

# ── shared in-memory repositories ──


class MemInputRepository:
    def __init__(self) -> None:
        self.records: list[InputRecord] = []

    def save(self, record: InputRecord) -> None:
        for i, r in enumerate(self.records):
            if r.id == record.id:
                self.records[i] = record
                return
        self.records.append(record)

    def get(self, input_id: str) -> InputRecord | None:
        return next((r for r in self.records if r.id == input_id), None)

    def find(self, **kwargs: Any) -> list[InputRecord]:
        return list(self.records)


class MemPredictionRepository:
    def __init__(self) -> None:
        self._predictions: list[PredictionRecord] = []
        self._configs: list[dict[str, Any]] = [
            {
                "id": "CFG_1",
                "scope_key": "BTC-60-60",
                "scope_template": {"subject": "BTC"},
                "schedule": {
                    "prediction_interval_seconds": 60,
                    "resolve_horizon_seconds": 60,
                },
                "active": True,
                "order": 1,
            },
        ]

    def save(self, prediction: PredictionRecord) -> None:
        for i, p in enumerate(self._predictions):
            if p.id == prediction.id:
                self._predictions[i] = prediction
                return
        self._predictions.append(prediction)

    def save_all(self, predictions: Any) -> None:
        for p in predictions:
            self.save(p)

    def find(
        self,
        *,
        status: str | list[str] | None = None,
        resolvable_before: datetime | None = None,
        **kwargs: Any,
    ) -> list[PredictionRecord]:
        results = list(self._predictions)
        if status is not None:
            statuses = status if isinstance(status, list) else [status]
            results = [p for p in results if p.status in statuses]
        if resolvable_before is not None:
            results = [
                p
                for p in results
                if p.resolvable_at and p.resolvable_at <= resolvable_before
            ]
        return results

    def fetch_active_configs(self) -> list[dict[str, Any]]:
        return self._configs

    @property
    def all(self) -> list[PredictionRecord]:
        return list(self._predictions)


class MemScoreRepository:
    def __init__(self) -> None:
        self.scores: list[ScoreRecord] = []

    def save(self, record: ScoreRecord) -> None:
        for i, s in enumerate(self.scores):
            if s.id == record.id:
                self.scores[i] = record
                return
        self.scores.append(record)

    def find(
        self,
        *,
        prediction_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        **kwargs: Any,
    ) -> list[ScoreRecord]:
        results = list(self.scores)
        if prediction_id is not None:
            results = [s for s in results if s.prediction_id == prediction_id]
        return results


class MemModelRepository:
    def __init__(self) -> None:
        self.models: dict[str, Model] = {}

    def save(self, model: Model) -> None:
        self.models[model.id] = model

    def fetch_all(self) -> dict[str, Model]:
        return dict(self.models)


class MemSnapshotRepository:
    def __init__(self) -> None:
        self.snapshots: list = []

    def save(self, record) -> None:
        self.snapshots.append(record)

    def find(self, *, model_id=None, since=None, until=None, limit=None) -> list:
        results = list(self.snapshots)
        if model_id is not None:
            results = [s for s in results if s.model_id == model_id]
        return results


class MemCheckpointRepository:
    def __init__(self) -> None:
        self.checkpoints: list[CheckpointRecord] = []

    def save(self, record: CheckpointRecord) -> None:
        existing = next((c for c in self.checkpoints if c.id == record.id), None)
        if existing:
            idx = self.checkpoints.index(existing)
            self.checkpoints[idx] = record
        else:
            self.checkpoints.append(record)

    def find(self, *, status=None, limit=None) -> list[CheckpointRecord]:
        results = list(self.checkpoints)
        if status:
            results = [c for c in results if c.status == status]
        results.sort(key=lambda c: c.created_at, reverse=True)
        return results[:limit] if limit else results

    def get_latest(self) -> CheckpointRecord | None:
        if not self.checkpoints:
            return None
        return sorted(self.checkpoints, key=lambda c: c.created_at, reverse=True)[0]


class MemLeaderboardRepository:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def save(
        self, entries: list[dict[str, Any]], meta: dict[str, Any] | None = None
    ) -> None:
        self.entries = entries

    def get_latest(self) -> list[dict[str, Any]]:
        return self.entries


# ── fakes for external boundaries ──


class FakeModelRun:
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.model_name = f"model-{model_id}"
        self.deployment_id = f"dep-{model_id}"
        self.infos = {
            "cruncher_id": f"p-{model_id}",
            "cruncher_name": f"Player {model_id}",
        }


class FakeResult:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.status = "SUCCESS"
        self.exec_time_us = 42


class FakeRunner:
    """Returns deterministic predictions from two models."""

    def __init__(self, outputs: dict[str, dict[str, Any]]) -> None:
        self._outputs = outputs

    async def init(self) -> None:
        pass

    async def sync(self) -> None:
        pass

    async def call(self, method: str, args: Any) -> dict:
        if method == "feed_update":
            return {FakeModelRun(mid): None for mid in self._outputs}
        return {
            FakeModelRun(mid): FakeResult(out) for mid, out in self._outputs.items()
        }


class FakeFeedRecord:
    def __init__(self, price: float, ts: datetime) -> None:
        self.source = "pyth"
        self.subject = "BTC"
        self.kind = "tick"
        self.granularity = "1s"
        self.ts_event = ts
        self.values = {"close": price}


class FakeFeedReader:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.source = "pyth"
        self.subject = "BTC"
        self.kind = "tick"
        self.granularity = "1s"

    def get_input(self, now: datetime) -> dict[str, Any]:
        return dict(self._data)

    def fetch_window(
        self,
        start=None,
        end=None,
        source=None,
        subject=None,
        kind=None,
        granularity=None,
    ) -> list:
        now = datetime.now(UTC)
        return [
            FakeFeedRecord(100.0, now - timedelta(minutes=5)),
            FakeFeedRecord(105.0, now - timedelta(minutes=1)),
        ]


# ── lifecycle test ──


class TestPredictionLifecycle(unittest.IsolatedAsyncioTestCase):
    """Full flow: input → predict → score → leaderboard, all in-memory."""

    def setUp(self) -> None:
        self.input_repo = MemInputRepository()
        self.pred_repo = MemPredictionRepository()
        self.score_repo = MemScoreRepository()
        self.snapshot_repo = MemSnapshotRepository()
        self.checkpoint_repo = MemCheckpointRepository()
        self.model_repo = MemModelRepository()
        self.lb_repo = MemLeaderboardRepository()
        self.config = CrunchConfig(crunch_pubkey="test_crunch_pubkey")

        self.predict_service = RealtimePredictService(
            checkpoint_interval_seconds=60,
            feed_reader=FakeFeedReader({"symbol": "BTC", "asof_ts": 100}),
            config=self.config,
            input_repository=self.input_repo,
            model_repository=self.model_repo,
            prediction_repository=self.pred_repo,
            runner=FakeRunner({"m1": {"value": 0.7}, "m2": {"value": 0.3}}),
        )

        self.score_service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=self._score_fn,
            feed_reader=FakeFeedReader({"symbol": "BTC", "asof_ts": 100}),
            input_repository=self.input_repo,
            prediction_repository=self.pred_repo,
            score_repository=self.score_repo,
            snapshot_repository=self.snapshot_repo,
            model_repository=self.model_repo,
            leaderboard_repository=self.lb_repo,
            config=self.config,
        )

        self.checkpoint_service = CheckpointService(
            snapshot_repository=self.snapshot_repo,
            checkpoint_repository=self.checkpoint_repo,
            model_repository=self.model_repo,
            build_emission=default_build_emission,
            crunch_pubkey=self.config.crunch_pubkey,
        )

    @staticmethod
    def _score_fn(prediction, ground_truth):
        pred_val = getattr(prediction, "value", 0)
        actual_return = getattr(ground_truth, "profit", 0)
        error = abs(pred_val - actual_return)
        return {
            "value": round(1.0 / (1.0 + error), 4),
            "success": True,
            "failed_reason": None,
        }

    async def test_full_lifecycle(self) -> None:
        now = datetime.now(UTC) - timedelta(minutes=5)

        # ── step 1: predict ──
        changed = await self.predict_service.process_tick(now=now)
        self.assertTrue(changed)

        # input saved
        self.assertEqual(len(self.input_repo.records), 1)
        inp = self.input_repo.records[0]
        self.assertIn("symbol", inp.raw_data)

        # predictions saved as PENDING
        predictions = self.pred_repo.all
        self.assertEqual(len(predictions), 2)  # m1, m2
        self.assertTrue(all(p.status == PredictionStatus.PENDING for p in predictions))
        self.assertTrue(all(p.input_id == inp.id for p in predictions))

        # models registered
        self.assertIn("m1", self.model_repo.models)
        self.assertIn("m2", self.model_repo.models)

        # ── step 2: score (resolves actuals + scores) ──
        scored = self.score_service.score_and_snapshot()
        self.assertTrue(scored)

        # predictions now SCORED
        scored_preds = self.pred_repo.find(status=PredictionStatus.SCORED)
        self.assertEqual(len(scored_preds), 2)

        # score records created
        self.assertEqual(len(self.score_repo.scores), 2)
        for score in self.score_repo.scores:
            self.assertIn("value", score.result)
            self.assertTrue(score.success)

        # leaderboard rebuilt with both models ranked
        self.assertEqual(len(self.lb_repo.entries), 2)
        ranks = [e["rank"] for e in self.lb_repo.entries]
        self.assertEqual(sorted(ranks), [1, 2])

    async def test_predict_twice_accumulates(self) -> None:
        now = datetime.now(UTC) - timedelta(minutes=5)

        await self.predict_service.process_tick(now=now)
        self.assertEqual(len(self.pred_repo.all), 2)

        # second run with different time (past schedule interval)
        later = now + timedelta(minutes=2)
        await self.predict_service.process_tick(now=later)
        self.assertEqual(len(self.pred_repo.all), 4)

        # all PENDING
        self.assertTrue(
            all(p.status == PredictionStatus.PENDING for p in self.pred_repo.all)
        )

    async def test_score_skips_when_no_pending(self) -> None:
        """Score service does nothing when there's nothing to score."""
        with self.assertLogs("crunch_node.services.score", level="INFO"):
            scored = self.score_service.score_and_snapshot()
        self.assertFalse(scored)
        self.assertEqual(len(self.score_repo.scores), 0)

    async def test_score_idempotent(self) -> None:
        """Running score twice doesn't re-score already scored predictions."""
        now = datetime.now(UTC) - timedelta(minutes=5)
        await self.predict_service.process_tick(now=now)

        self.score_service.score_and_snapshot()
        self.assertEqual(len(self.score_repo.scores), 2)

        # second score run — nothing new to score
        with self.assertLogs("crunch_node.services.score", level="INFO"):
            scored = self.score_service.score_and_snapshot()
        self.assertFalse(scored)
        self.assertEqual(len(self.score_repo.scores), 2)  # unchanged

    async def test_absent_model_marked(self) -> None:
        """If a known model doesn't respond, it gets an ABSENT prediction."""
        # First run registers both models
        now = datetime.now(UTC) - timedelta(minutes=5)
        await self.predict_service.process_tick(now=now)

        # Swap to a runner that only returns m1
        self.predict_service._runner = FakeRunner({"m1": {"value": 0.5}})

        later = now + timedelta(minutes=2)
        await self.predict_service.process_tick(now=later)

        # Should have 4 total: 2 from first run + 2 from second (m1 PENDING + m2 ABSENT)
        all_preds = self.pred_repo.all
        self.assertEqual(len(all_preds), 4)
        absent = [p for p in all_preds if p.status == PredictionStatus.ABSENT]
        self.assertEqual(len(absent), 1)
        self.assertEqual(absent[0].model_id, "m2")

    async def test_input_ids_are_unique(self) -> None:
        now = datetime.now(UTC)
        await self.predict_service.process_tick(now=now)
        later = now + timedelta(minutes=2)
        await self.predict_service.process_tick(now=later)

        ids = [r.id for r in self.input_repo.records]
        self.assertEqual(len(ids), len(set(ids)), "Input IDs should be unique")

    async def test_snapshots_written_after_scoring(self) -> None:
        """Score cycle writes snapshots per model."""
        now = datetime.now(UTC) - timedelta(minutes=5)
        await self.predict_service.process_tick(now=now)
        self.score_service.score_and_snapshot()

        self.assertEqual(len(self.snapshot_repo.snapshots), 2)  # one per model

        snap_model_ids = {s.model_id for s in self.snapshot_repo.snapshots}
        self.assertEqual(snap_model_ids, {"m1", "m2"})

        for snap in self.snapshot_repo.snapshots:
            self.assertGreater(snap.prediction_count, 0)
            self.assertIn("value", snap.result_summary)

    async def test_full_pipeline_predict_to_checkpoint(self) -> None:
        """End-to-end: predict → score → snapshot → checkpoint with prize distribution."""
        now = datetime.now(UTC) - timedelta(minutes=5)

        # ── predict ──
        await self.predict_service.process_tick(now=now)
        self.assertEqual(len(self.pred_repo.all), 2)

        # ── score (resolves actuals + scores + writes snapshots) ──
        self.score_service.score_and_snapshot()
        self.assertEqual(len(self.score_repo.scores), 2)
        self.assertEqual(len(self.snapshot_repo.snapshots), 2)

        # ── checkpoint (aggregates snapshots → emission checkpoint) ──
        checkpoint = self.checkpoint_service.create_checkpoint()

        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.status, CheckpointStatus.PENDING)

        # Single EmissionCheckpoint entry
        self.assertEqual(len(checkpoint.entries), 1)
        emission = checkpoint.entries[0]
        self.assertEqual(emission["crunch"], "test_crunch_pubkey")
        self.assertIn("cruncher_rewards", emission)
        self.assertIn("compute_provider_rewards", emission)
        self.assertIn("data_provider_rewards", emission)

        # 2 cruncher rewards (one per model)
        self.assertEqual(len(emission["cruncher_rewards"]), 2)

        # Rewards sum to exactly FRAC_64_MULTIPLIER (100%)
        total_pct = sum(r["reward_pct"] for r in emission["cruncher_rewards"])
        self.assertEqual(total_pct, FRAC_64_MULTIPLIER)

        # 1st place gets more than 2nd
        self.assertGreater(
            emission["cruncher_rewards"][0]["reward_pct"],
            emission["cruncher_rewards"][1]["reward_pct"],
        )

        # Ranking in meta
        self.assertIn("ranking", checkpoint.meta)
        self.assertEqual(checkpoint.meta["ranking"][0]["rank"], 1)
        self.assertEqual(checkpoint.meta["ranking"][1]["rank"], 2)

    async def test_leaderboard_ranking_order(self) -> None:
        """Model with higher score should rank first (default desc)."""
        now = datetime.now(UTC) - timedelta(minutes=5)
        await self.predict_service.process_tick(now=now)
        self.score_service.score_and_snapshot()

        # m1 predicted 0.7, m2 predicted 0.3, actual_return=0.05
        # score = 1/(1+|pred-actual_return|)
        # m1: 1/(1+0.65) ≈ 0.606, m2: 1/(1+0.25) = 0.8
        # m2 is closer to actual → higher score → rank 1
        entries = self.lb_repo.entries
        self.assertEqual(entries[0]["model_id"], "m2")
        self.assertEqual(entries[0]["rank"], 1)


if __name__ == "__main__":
    unittest.main()
