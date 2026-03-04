"""Tests for checkpoint creation integrated into the score service."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.prediction import (
    CheckpointRecord,
    CheckpointStatus,
    InputRecord,
    PredictionRecord,
    PredictionStatus,
    ScoreRecord,
    SnapshotRecord,
)
from crunch_node.services.score import ScoreService

now = datetime.now(UTC)


# ── In-memory repositories ──


class MemInputRepository:
    def __init__(self):
        self.records: list[InputRecord] = []

    def save(self, record):
        self.records.append(record)

    def get(self, input_id):
        return next((r for r in self.records if r.id == input_id), None)


class MemPredictionRepository:
    def __init__(self, predictions: list[PredictionRecord] | None = None):
        self.predictions = list(predictions or [])

    def save(self, prediction):
        for i, p in enumerate(self.predictions):
            if p.id == prediction.id:
                self.predictions[i] = prediction
                return
        self.predictions.append(prediction)

    def save_all(self, predictions):
        for p in predictions:
            self.save(p)

    def find(self, *, status=None, resolvable_before=None, **kwargs):
        results = list(self.predictions)
        if status is not None:
            if isinstance(status, list):
                results = [p for p in results if p.status in status]
            else:
                results = [p for p in results if p.status == status]
        if resolvable_before is not None:
            results = [
                p
                for p in results
                if p.resolvable_at and p.resolvable_at <= resolvable_before
            ]
        return results


class MemScoreRepository:
    def __init__(self):
        self.scores: list[ScoreRecord] = []

    def save(self, score):
        self.scores.append(score)


class MemSnapshotRepository:
    def __init__(self, snapshots: list[SnapshotRecord] | None = None):
        self.snapshots = list(snapshots or [])

    def save(self, record):
        self.snapshots.append(record)

    def find(self, *, model_id=None, since=None, until=None, limit=None):
        results = list(self.snapshots)
        if model_id:
            results = [s for s in results if s.model_id == model_id]
        if since:
            results = [s for s in results if s.period_end >= since]
        if until:
            results = [s for s in results if s.period_start <= until]
        return results


class MemModelRepository:
    def __init__(self):
        self.models = {}

    def save(self, model):
        self.models[model.id] = model

    def fetch_all(self):
        return dict(self.models)


class MemLeaderboardRepository:
    def __init__(self):
        self.entries = []

    def save(self, entries, meta=None):
        self.entries = entries


class MemCheckpointRepository:
    def __init__(self, checkpoints: list[CheckpointRecord] | None = None):
        self.checkpoints = list(checkpoints or [])

    def save(self, record):
        existing = next((c for c in self.checkpoints if c.id == record.id), None)
        if existing:
            idx = self.checkpoints.index(existing)
            self.checkpoints[idx] = record
        else:
            self.checkpoints.append(record)

    def get_latest(self):
        if not self.checkpoints:
            return None
        return sorted(self.checkpoints, key=lambda c: c.created_at, reverse=True)[0]

    def update_merkle_root(self, checkpoint_id, merkle_root):
        pass

    def find(self, *, status=None, limit=None):
        results = list(self.checkpoints)
        if status:
            results = [c for c in results if c.status == status]
        if limit:
            results = results[:limit]
        return results


# ── Helpers ──


_pred_counter = 0


def _make_prediction(
    model_id: str = "m1",
    value: float = 0.5,
    performed_at: datetime | None = None,
    resolvable_at: datetime | None = None,
) -> PredictionRecord:
    global _pred_counter
    _pred_counter += 1
    t = performed_at or now - timedelta(minutes=5)
    return PredictionRecord(
        id=f"PRE_{model_id}_{_pred_counter}",
        input_id=f"INP_{model_id}_{_pred_counter}",
        model_id=model_id,
        prediction_config_id=None,
        scope_key="BTC-60",
        scope={"subject": "BTC"},
        status=PredictionStatus.PENDING,
        exec_time_ms=0.0,
        inference_output={"value": value},
        performed_at=t,
        resolvable_at=resolvable_at or t,  # immediate resolution
    )


def _make_input(prediction: PredictionRecord) -> InputRecord:
    return InputRecord(
        id=prediction.input_id,
        raw_data={
            "entry_price": 40000,
            "resolved_price": 40100,
            "profit": 0.0025,
            "direction_up": True,
        },
        received_at=prediction.performed_at,
    )


def _scoring_function(prediction: dict, ground_truth: dict) -> dict:
    return {"value": 1.0, "success": True}


def _build_service(
    predictions: list[PredictionRecord] | None = None,
    checkpoint_repo: MemCheckpointRepository | None = None,
    checkpoint_interval: int = 604800,
) -> tuple[ScoreService, MemCheckpointRepository]:
    pred_repo = MemPredictionRepository(predictions or [])
    input_repo = MemInputRepository()
    snap_repo = MemSnapshotRepository()
    ckpt_repo = checkpoint_repo or MemCheckpointRepository()

    # Save inputs for immediate resolution
    for p in pred_repo.predictions:
        input_repo.save(_make_input(p))

    service = ScoreService(
        checkpoint_interval_seconds=checkpoint_interval,
        score_interval_seconds=60,
        scoring_function=_scoring_function,
        input_repository=input_repo,
        prediction_repository=pred_repo,
        score_repository=MemScoreRepository(),
        snapshot_repository=snap_repo,
        model_repository=MemModelRepository(),
        leaderboard_repository=MemLeaderboardRepository(),
        checkpoint_repository=ckpt_repo,
        contract=CrunchConfig(crunch_pubkey="crunch_test"),
    )
    return service, ckpt_repo


# ── Tests ──


class TestScoreServiceCheckpointIntegration(unittest.TestCase):
    def test_checkpoint_created_when_interval_elapsed(self):
        """Checkpoint is created after scoring when interval has passed."""
        predictions = [_make_prediction("m1"), _make_prediction("m2")]
        service, ckpt_repo = _build_service(
            predictions=predictions,
            checkpoint_interval=1,  # 1 second — will always have elapsed
        )
        # Force _last_checkpoint_at to be well in the past
        service._last_checkpoint_at = now - timedelta(hours=1)

        service.run_once()

        self.assertEqual(len(ckpt_repo.checkpoints), 1)
        self.assertEqual(ckpt_repo.checkpoints[0].status, CheckpointStatus.PENDING)

    def test_no_checkpoint_when_interval_not_elapsed(self):
        """No checkpoint when interval hasn't elapsed yet."""
        predictions = [_make_prediction("m1")]

        # Create a recent checkpoint so interval hasn't elapsed
        recent_checkpoint = CheckpointRecord(
            id="CKP_recent",
            period_start=now - timedelta(hours=1),
            period_end=now - timedelta(seconds=10),
            status=CheckpointStatus.PENDING,
            created_at=now - timedelta(seconds=10),
        )
        ckpt_repo = MemCheckpointRepository([recent_checkpoint])
        service, ckpt_repo = _build_service(
            predictions=predictions,
            checkpoint_repo=ckpt_repo,
            checkpoint_interval=604800,  # weekly
        )

        service.run_once()

        # Only the pre-existing checkpoint
        self.assertEqual(len(ckpt_repo.checkpoints), 1)
        self.assertEqual(ckpt_repo.checkpoints[0].id, "CKP_recent")

    def test_no_checkpoint_when_no_scores(self):
        """No checkpoint attempt when nothing was scored."""
        service, ckpt_repo = _build_service(
            predictions=[],
            checkpoint_interval=1,
        )

        result = service.run_once()

        self.assertFalse(result)
        self.assertEqual(len(ckpt_repo.checkpoints), 0)

    def test_checkpoint_not_created_without_checkpoint_repo(self):
        """ScoreService works fine without checkpoint_repository (backward compat)."""
        predictions = [_make_prediction("m1")]
        pred_repo = MemPredictionRepository(predictions)
        input_repo = MemInputRepository()
        for p in predictions:
            input_repo.save(_make_input(p))

        service = ScoreService(
            checkpoint_interval_seconds=0,
            score_interval_seconds=60,
            scoring_function=_scoring_function,
            input_repository=input_repo,
            prediction_repository=pred_repo,
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
            # No checkpoint_repository
            contract=CrunchConfig(),
        )

        # Should not raise
        result = service.run_once()
        self.assertTrue(result)

    def test_checkpoint_service_accessible(self):
        """The composed CheckpointService is accessible for direct use."""
        service, _ = _build_service(checkpoint_interval=3600)
        self.assertIsNotNone(service._checkpoint_service)

    def test_last_checkpoint_at_updated_after_creation(self):
        """_last_checkpoint_at is updated so next cycle doesn't re-checkpoint."""
        predictions = [_make_prediction("m1")]
        service, ckpt_repo = _build_service(
            predictions=predictions,
            checkpoint_interval=1,
        )
        service._last_checkpoint_at = now - timedelta(hours=1)

        service.run_once()

        self.assertIsNotNone(service._last_checkpoint_at)
        self.assertEqual(len(ckpt_repo.checkpoints), 1)

    def test_checkpoint_has_emission_entries(self):
        """Created checkpoint contains emission entries."""
        predictions = [_make_prediction("m1"), _make_prediction("m2")]
        service, ckpt_repo = _build_service(
            predictions=predictions,
            checkpoint_interval=1,
        )
        service._last_checkpoint_at = now - timedelta(hours=1)

        service.run_once()

        checkpoint = ckpt_repo.checkpoints[0]
        self.assertEqual(len(checkpoint.entries), 1)
        emission = checkpoint.entries[0]
        self.assertEqual(emission["crunch"], "crunch_test")
        self.assertGreater(len(emission["cruncher_rewards"]), 0)


if __name__ == "__main__":
    unittest.main()
