from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from pydantic import BaseModel, ConfigDict

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.feed_record import FeedRecord
from crunch_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    PredictionStatus,
    ScoreRecord,
    SnapshotRecord,
)
from crunch_node.services.prediction_scorer import PredictionScorer


class MemInputRepository:
    def __init__(self, records: list[InputRecord] | None = None) -> None:
        self._records = list(records or [])

    def save(self, record: InputRecord) -> None:
        for i, r in enumerate(self._records):
            if r.id == record.id:
                self._records[i] = record
                return
        self._records.append(record)

    def get(self, input_id: str) -> InputRecord | None:
        return next((r for r in self._records if r.id == input_id), None)


class MemPredictionRepository:
    def __init__(self, predictions: list[PredictionRecord] | None = None) -> None:
        self._predictions = list(predictions or [])

    def find(
        self,
        *,
        status: str | None = None,
        resolvable_before: datetime | None = None,
        **kwargs: Any,
    ) -> list[PredictionRecord]:
        results = list(self._predictions)
        if status is not None:
            results = [p for p in results if p.status == status]
        if resolvable_before is not None:
            results = [
                p
                for p in results
                if p.resolvable_at and p.resolvable_at <= resolvable_before
            ]
        return results

    def save(self, prediction: PredictionRecord) -> None:
        for i, p in enumerate(self._predictions):
            if p.id == prediction.id:
                self._predictions[i] = prediction
                return
        self._predictions.append(prediction)


class MemScoreRepository:
    def __init__(self) -> None:
        self.scores: list[ScoreRecord] = []

    def save(self, record: ScoreRecord) -> None:
        for i, s in enumerate(self.scores):
            if s.id == record.id:
                self.scores[i] = record
                return
        self.scores.append(record)


class MemSnapshotRepository:
    def __init__(self) -> None:
        self.snapshots: list[SnapshotRecord] = []

    def save(self, record: SnapshotRecord) -> None:
        self.snapshots.append(record)

    def find(self, *, model_id=None, since=None, until=None, limit=None) -> list:
        results = list(self.snapshots)
        if model_id is not None:
            results = [s for s in results if s.model_id == model_id]
        return results


class FakeFeedReader:
    def __init__(self, records: list | None = None) -> None:
        self._records = records or []

    def fetch_window(
        self,
        start=None,
        end=None,
        source=None,
        subject=None,
        kind=None,
        granularity=None,
    ) -> list:
        return self._records


now = datetime.now(UTC)


def _make_input() -> InputRecord:
    return InputRecord(
        id="inp-1",
        raw_data={"symbol": "BTC"},
        received_at=now - timedelta(minutes=5),
    )


def _make_prediction(
    input_id: str = "inp-1", status: str = "PENDING"
) -> PredictionRecord:
    return PredictionRecord(
        id="pre-1",
        input_id=input_id,
        model_id="m1",
        prediction_config_id="CFG_1",
        scope_key="BTC-60",
        scope={
            "subject": "BTC",
            "source": "pyth",
            "kind": "tick",
            "granularity": "1s",
        },
        status=status,
        exec_time_ms=10.0,
        inference_output={"value": 0.5},
        performed_at=now - timedelta(minutes=5),
        resolvable_at=now - timedelta(minutes=1),
    )


def _make_feed_records(
    entry_price: float = 100.0, resolved_price: float = 105.0
) -> list[FeedRecord]:
    return [
        FeedRecord(
            source="pyth",
            subject="BTC",
            kind="tick",
            granularity="1s",
            ts_event=now - timedelta(minutes=5),
            values={"close": entry_price},
        ),
        FeedRecord(
            source="pyth",
            subject="BTC",
            kind="tick",
            granularity="1s",
            ts_event=now - timedelta(minutes=1),
            values={"close": resolved_price},
        ),
    ]


DEFAULT_SCORER = lambda pred, act: {
    "value": 0.5,
    "success": True,
    "failed_reason": None,
}


def _build_scorer(
    *,
    inputs=None,
    predictions=None,
    feed_records=None,
    config=None,
    scoring_function=None,
) -> PredictionScorer:
    return PredictionScorer(
        scoring_function=scoring_function or DEFAULT_SCORER,
        feed_reader=FakeFeedReader(records=feed_records or []),
        input_repository=MemInputRepository(inputs or []),
        prediction_repository=MemPredictionRepository(predictions or []),
        score_repository=MemScoreRepository(),
        snapshot_repository=MemSnapshotRepository(),
        config=config,
    )


class TestProduceSnapshots(unittest.TestCase):
    def test_scores_and_aggregates(self):
        scorer = _build_scorer(
            inputs=[_make_input()],
            predictions=[_make_prediction()],
            feed_records=_make_feed_records(),
        )

        snapshots = scorer.produce_snapshots(now)

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].model_id, "m1")
        self.assertEqual(snapshots[0].prediction_count, 1)
        self.assertEqual(len(scorer.score_repository.scores), 1)
        self.assertEqual(scorer.score_repository.scores[0].result["value"], 0.5)

    def test_returns_empty_when_no_predictions(self):
        scorer = _build_scorer()
        snapshots = scorer.produce_snapshots(now)
        self.assertEqual(snapshots, [])

    def test_returns_empty_when_no_actuals(self):
        scorer = _build_scorer(
            inputs=[_make_input()],
            predictions=[_make_prediction()],
            feed_records=[],
        )
        snapshots = scorer.produce_snapshots(now)
        self.assertEqual(snapshots, [])
        self.assertEqual(len(scorer.score_repository.scores), 0)

    def test_idempotent(self):
        scorer = _build_scorer(
            inputs=[_make_input()],
            predictions=[_make_prediction()],
            feed_records=_make_feed_records(),
        )

        first = scorer.produce_snapshots(now)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(scorer.score_repository.scores), 1)

        second = scorer.produce_snapshots(now)
        self.assertEqual(second, [])
        self.assertEqual(len(scorer.score_repository.scores), 1)


class TestValidateScoringIO(unittest.TestCase):
    def test_passes_for_compatible_types(self):
        scorer = _build_scorer()
        scorer.validate_scoring_io()

    def test_catches_mismatch(self):
        def bad_scorer(prediction, ground_truth):
            return {"value": prediction.order_type}

        scorer = _build_scorer(scoring_function=bad_scorer)

        with self.assertRaises(RuntimeError) as ctx:
            scorer.validate_scoring_io()

        self.assertIn("AttributeError", str(ctx.exception))
        self.assertIn("order_type", str(ctx.exception))


class TestCoerceOutput(unittest.TestCase):
    def test_preserves_values(self):
        scorer = _build_scorer()
        result = scorer._coerce_output({"value": 1.23})
        self.assertAlmostEqual(result.value, 1.23)

    def test_coerces_string_to_float(self):
        scorer = _build_scorer()
        result = scorer._coerce_output({"value": "0.5"})
        self.assertAlmostEqual(result.value, 0.5)

    def test_fills_missing_fields_with_defaults(self):
        scorer = _build_scorer()
        result = scorer._coerce_output({})
        self.assertAlmostEqual(result.value, 0.0)


class TestScorerReceivesPredictionMetadata(unittest.TestCase):
    def test_model_id_injected(self):
        captured = []

        def capturing_scorer(prediction, ground_truth):
            captured.append(prediction.__dict__.copy())
            return {"value": 0.0, "success": True, "failed_reason": None}

        scorer = PredictionScorer(
            scoring_function=capturing_scorer,
            feed_reader=FakeFeedReader(records=_make_feed_records()),
            input_repository=MemInputRepository([_make_input()]),
            prediction_repository=MemPredictionRepository(
                [
                    PredictionRecord(
                        id="pre-1",
                        input_id="inp-1",
                        model_id="model_42",
                        prediction_config_id="CFG_1",
                        scope_key="BTC-60",
                        scope={"subject": "BTC"},
                        status="PENDING",
                        exec_time_ms=10.0,
                        inference_output={"value": 0.5},
                        performed_at=now - timedelta(minutes=5),
                        resolvable_at=now - timedelta(minutes=1),
                    ),
                ]
            ),
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
        )

        scorer.produce_snapshots(now)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["model_id"], "model_42")
        self.assertIn("prediction_id", captured[0])


class TestRollback(unittest.TestCase):
    def test_delegates_to_repos(self):
        scorer = _build_scorer()

        mock_repo = MagicMock()
        mock_repo.rollback = MagicMock()
        scorer.prediction_repository = mock_repo

        scorer.rollback()

        mock_repo.rollback.assert_called_once()

    def test_skips_repos_without_rollback(self):
        scorer = _build_scorer()
        scorer.rollback()

    def test_continues_on_rollback_error(self):
        scorer = _build_scorer()

        failing_repo = MagicMock()
        failing_repo.rollback = MagicMock(side_effect=RuntimeError("db error"))
        scorer.prediction_repository = failing_repo

        ok_repo = MagicMock()
        ok_repo.rollback = MagicMock()
        scorer.score_repository = ok_repo

        with self.assertLogs("crunch_node.services.prediction_scorer", level="WARNING"):
            scorer.rollback()

        ok_repo.rollback.assert_called_once()


class TestDetectScoringStub(unittest.TestCase):
    def test_detects_constant_scorer(self):
        def stub(pred, gt):
            return {"value": 1.0}

        is_stub, reason = PredictionScorer.detect_scoring_stub(stub)
        self.assertTrue(is_stub)
        self.assertIn("identical value", reason)

    def test_passes_real_scorer(self):
        def real(pred, gt):
            return {"value": pred.get("value", 0) * 2}

        is_stub, reason = PredictionScorer.detect_scoring_stub(real)
        self.assertFalse(is_stub)
        self.assertEqual(reason, "ok")


if __name__ == "__main__":
    unittest.main()
