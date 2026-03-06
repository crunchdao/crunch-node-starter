from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from crunch_node.crunch_config import Aggregation, AggregationWindow, CrunchConfig
from crunch_node.entities.feed_record import FeedRecord
from crunch_node.entities.model import Model
from crunch_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    ScoreRecord,
)
from crunch_node.services.score import ScoreService


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

    def find(self, **kwargs: Any) -> list[InputRecord]:
        return list(self._records)


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

    def save_all(self, predictions: Any) -> None:
        for p in predictions:
            self.save(p)


class MemScoreRepository:
    def __init__(self) -> None:
        self.scores: list[ScoreRecord] = []

    def save(self, record: ScoreRecord) -> None:
        for i, s in enumerate(self.scores):
            if s.id == record.id:
                self.scores[i] = record
                return
        self.scores.append(record)

    def find(self, **kwargs: Any) -> list[ScoreRecord]:
        return list(self.scores)


class MemModelRepository:
    def __init__(self) -> None:
        self.models = {
            "m1": Model(
                id="m1",
                name="model-one",
                player_id="p1",
                player_name="alice",
                deployment_identifier="d1",
            )
        }

    def fetch_all(self) -> dict[str, Model]:
        return self.models


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


class MemLeaderboardRepository:
    def __init__(self) -> None:
        self.latest: Any = None

    def save(self, entries: Any, meta: Any = None) -> None:
        self.latest = {"entries": entries, "meta": meta or {}}

    def get_latest(self) -> Any:
        return self.latest


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


def _build_service(*, inputs=None, predictions=None, feed_records=None, config=None):
    return ScoreService(
        checkpoint_interval_seconds=60,
        scoring_function=lambda pred, act: {
            "value": 0.5,
            "success": True,
            "failed_reason": None,
        },
        feed_reader=FakeFeedReader(records=feed_records or []),
        input_repository=MemInputRepository(inputs or []),
        prediction_repository=MemPredictionRepository(predictions or []),
        score_repository=MemScoreRepository(),
        snapshot_repository=MemSnapshotRepository(),
        model_repository=MemModelRepository(),
        leaderboard_repository=MemLeaderboardRepository(),
        config=config,
    )


class TestScoreService(unittest.TestCase):
    def test_resolve_inputs_then_score(self):
        service = _build_service(
            inputs=[_make_input()],
            predictions=[_make_prediction()],
            feed_records=_make_feed_records(),
        )

        changed = service.run_once()

        self.assertTrue(changed)
        self.assertEqual(len(service.score_repository.scores), 1)
        self.assertEqual(service.score_repository.scores[0].result["value"], 0.5)

    def test_no_actuals_means_no_scoring(self):
        service = _build_service(
            inputs=[_make_input()],
            predictions=[_make_prediction()],
            feed_records=[],
        )

        with self.assertLogs("crunch_node.services.score", level="INFO"):
            changed = service.run_once()

        self.assertFalse(changed)
        self.assertEqual(len(service.score_repository.scores), 0)

    def test_no_predictions_means_no_scoring(self):
        service = _build_service()

        with self.assertLogs("crunch_node.services.score", level="INFO"):
            changed = service.run_once()

        self.assertFalse(changed)

    def test_idempotent(self):
        service = _build_service(
            inputs=[_make_input()],
            predictions=[_make_prediction()],
            feed_records=_make_feed_records(),
        )

        service.run_once()
        self.assertEqual(len(service.score_repository.scores), 1)

        with self.assertLogs("crunch_node.services.score", level="INFO"):
            changed = service.run_once()
        self.assertFalse(changed)
        self.assertEqual(len(service.score_repository.scores), 1)

    def test_rank_ascending(self):
        contract = CrunchConfig(
            aggregation=Aggregation(
                windows={"loss": AggregationWindow(hours=24)},
                ranking_key="loss",
                ranking_direction="asc",
            )
        )
        service = _build_service(config=contract)

        ranked = service._rank_leaderboard(
            [
                {
                    "model_id": "m1",
                    "score": {"metrics": {"loss": 0.4}, "ranking": {}, "payload": {}},
                },
                {
                    "model_id": "m2",
                    "score": {"metrics": {"loss": 0.2}, "ranking": {}, "payload": {}},
                },
            ]
        )
        self.assertEqual([e["model_id"] for e in ranked], ["m2", "m1"])
        self.assertEqual([e["rank"] for e in ranked], [1, 2])


class TestScoreServiceRunLoop(unittest.IsolatedAsyncioTestCase):
    async def test_rollback_on_exception(self):
        service = _build_service()

        def boom():
            service.stop_event.set()
            raise RuntimeError("boom")

        service.run_once = boom

        with self.assertLogs("crunch_node.services.score", level="ERROR"):
            await service.run()


# ---------------------------------------------------------------------------
# InferenceOutput coercion & scoring IO validation
# ---------------------------------------------------------------------------


class TestCoerceOutput(unittest.TestCase):
    """ScoreService._coerce_output should parse raw dicts through output_type."""

    def test_default_output_fills_missing_fields(self):
        """If model returns {} the default InferenceOutput(value=0.0) fills it."""
        service = _build_service()
        result = service._coerce_output({})
        self.assertEqual(result, {"value": 0.0})

    def test_coercion_preserves_model_values(self):
        service = _build_service()
        result = service._coerce_output({"value": 1.23})
        self.assertAlmostEqual(result["value"], 1.23)

    def test_coercion_preserves_extra_keys(self):
        """Extra keys from the model (not in InferenceOutput) are kept."""
        service = _build_service()
        result = service._coerce_output({"value": 0.5, "confidence": 0.9})
        self.assertAlmostEqual(result["value"], 0.5)
        self.assertAlmostEqual(result["confidence"], 0.9)

    def test_coercion_with_custom_output_type(self):
        from pydantic import BaseModel, Field

        class TradingOutput(BaseModel):
            order_type: str = "HOLD"
            leverage: float = Field(default=1.0)

        contract = CrunchConfig(output_type=TradingOutput)
        service = _build_service(config=contract)

        # Model returns partial output — defaults should fill in
        result = service._coerce_output({"order_type": "LONG"})
        self.assertEqual(result["order_type"], "LONG")
        self.assertEqual(result["leverage"], 1.0)

    def test_coercion_type_coerces_values(self):
        """String '0.5' should be coerced to float 0.5."""
        service = _build_service()
        result = service._coerce_output({"value": "0.5"})
        self.assertAlmostEqual(result["value"], 0.5)

    def test_coercion_falls_back_on_validation_error(self):
        from pydantic import BaseModel, Field

        class StrictOutput(BaseModel):
            value: float = Field(ge=0.0, le=1.0)

        contract = CrunchConfig(output_type=StrictOutput)
        service = _build_service(config=contract)

        # value=999 violates ge/le constraint — should fall back to raw dict
        with self.assertLogs("crunch_node.services.score", level="WARNING"):
            result = service._coerce_output({"value": 999})
        self.assertEqual(result["value"], 999)


class TestScoringReceivesTypedOutput(unittest.TestCase):
    """The scoring function should receive a coerced dict, not raw model output."""

    def test_scorer_receives_typed_dict_with_defaults(self):
        from pydantic import BaseModel

        class CustomOutput(BaseModel):
            direction: str = "HOLD"
            confidence: float = 0.5

        captured = {}

        def capturing_scorer(prediction, ground_truth):
            captured.update(prediction)
            return {"value": 0.0, "success": True, "failed_reason": None}

        contract = CrunchConfig(output_type=CustomOutput)
        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=capturing_scorer,
            feed_reader=FakeFeedReader(records=_make_feed_records()),
            input_repository=MemInputRepository([_make_input()]),
            prediction_repository=MemPredictionRepository(
                [
                    PredictionRecord(
                        id="pre-1",
                        input_id="inp-1",
                        model_id="m1",
                        prediction_config_id="CFG_1",
                        scope_key="BTC-60",
                        scope={"subject": "BTC"},
                        status="PENDING",
                        exec_time_ms=10.0,
                        # Model only returned direction — confidence should get its default
                        inference_output={"direction": "LONG"},
                        performed_at=now - timedelta(minutes=5),
                        resolvable_at=now - timedelta(minutes=1),
                    ),
                ]
            ),
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
            config=contract,
        )

        service.run_once()

        # Scorer should have received the coerced dict with defaults
        self.assertEqual(captured["direction"], "LONG")
        self.assertEqual(captured["confidence"], 0.5)


class TestValidateScoringIO(unittest.TestCase):
    """ScoreService.validate_scoring_io() catches mismatches at startup."""

    def test_passes_for_compatible_types(self):
        """Default InferenceOutput + default scorer should pass."""
        service = _build_service()
        # Should not raise
        service.validate_scoring_io()

    def test_passes_for_custom_compatible_types(self):
        from pydantic import BaseModel

        class TradeOutput(BaseModel):
            order_type: str = "HOLD"
            leverage: float = 1.0

        def trade_scorer(prediction, ground_truth):
            ot = prediction["order_type"]  # noqa: F841
            lev = prediction["leverage"]  # noqa: F841
            return {"value": 0.0, "success": True, "failed_reason": None}

        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=trade_scorer,
            feed_reader=FakeFeedReader(),
            input_repository=MemInputRepository(),
            prediction_repository=MemPredictionRepository(),
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
            config=CrunchConfig(output_type=TradeOutput),
        )

        # Should not raise
        service.validate_scoring_io()

    def test_catches_key_mismatch(self):
        """Scorer reads 'order_type' but InferenceOutput only has 'value'."""

        def bad_scorer(prediction, ground_truth):
            return {"value": prediction["order_type"]}  # KeyError!

        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=bad_scorer,
            feed_reader=FakeFeedReader(),
            input_repository=MemInputRepository(),
            prediction_repository=MemPredictionRepository(),
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
        )

        with self.assertRaises(RuntimeError) as ctx:
            service.validate_scoring_io()

        self.assertIn("KeyError", str(ctx.exception))
        self.assertIn("order_type", str(ctx.exception))

    def test_catches_bad_score_result(self):
        """Scorer returns keys that don't match ScoreResult."""
        from pydantic import BaseModel, ConfigDict

        class StrictScoreResult(BaseModel):
            model_config = ConfigDict(extra="forbid")
            value: float = 0.0
            success: bool = True
            failed_reason: str | None = None

        def scorer_with_typo(prediction, ground_truth):
            return {"valeu": 0.5, "success": True}  # typo in 'value'

        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=scorer_with_typo,
            feed_reader=FakeFeedReader(),
            input_repository=MemInputRepository(),
            prediction_repository=MemPredictionRepository(),
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
            config=CrunchConfig(score_type=StrictScoreResult),
        )

        with self.assertRaises(RuntimeError) as ctx:
            service.validate_scoring_io()

        self.assertIn("ScoreResult", str(ctx.exception))

    def test_warns_on_non_keyerror_exceptions(self):
        """Non-KeyError exceptions in the scorer produce a warning, not a crash."""
        from pydantic import BaseModel

        class GroundTruthWithPrice(BaseModel):
            entry_price: float = 0.0

        def scorer_needs_real_data(prediction, ground_truth):
            # This scorer needs real prices that defaults don't provide
            # entry_price defaults to 0.0, causing ZeroDivisionError
            return {"value": prediction["value"] / ground_truth["entry_price"]}

        config = CrunchConfig(ground_truth_type=GroundTruthWithPrice)
        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=scorer_needs_real_data,
            feed_reader=FakeFeedReader(),
            input_repository=MemInputRepository(),
            prediction_repository=MemPredictionRepository(),
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
            contract=config,
        )

        # Should warn but not crash — ZeroDivisionError with entry_price=0.0 default
        with self.assertLogs("crunch_node.services.score", level="WARNING") as log:
            service.validate_scoring_io()
        self.assertTrue(any("ZeroDivisionError" in msg for msg in log.output))


class TestScorerReceivesPredictionMetadata(unittest.TestCase):
    """The scoring function should receive prediction metadata (model_id,
    prediction_id) so stateful scorers can track per-model state."""

    def test_scorer_receives_model_id(self):
        """typed_output passed to scoring_function must include model_id
        from the prediction entity."""
        captured = []

        def capturing_scorer(prediction, ground_truth):
            captured.append(dict(prediction))
            return {"value": 0.0, "success": True, "failed_reason": None}

        service = ScoreService(
            checkpoint_interval_seconds=60,
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
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
        )

        service.run_once()

        self.assertEqual(len(captured), 1)
        self.assertIn("model_id", captured[0])
        self.assertEqual(captured[0]["model_id"], "model_42")

    def test_scorer_receives_prediction_id(self):
        """typed_output should also include prediction_id for traceability."""
        captured = []

        def capturing_scorer(prediction, ground_truth):
            captured.append(dict(prediction))
            return {"value": 0.0, "success": True, "failed_reason": None}

        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=capturing_scorer,
            feed_reader=FakeFeedReader(records=_make_feed_records()),
            input_repository=MemInputRepository([_make_input()]),
            prediction_repository=MemPredictionRepository([_make_prediction()]),
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
        )

        service.run_once()

        self.assertEqual(len(captured), 1)
        self.assertIn("prediction_id", captured[0])
        self.assertEqual(captured[0]["prediction_id"], "pre-1")

    def test_different_models_receive_their_own_ids(self):
        """Two predictions from different models should each receive
        their respective model_id."""
        captured = []

        def capturing_scorer(prediction, ground_truth):
            captured.append(prediction.get("model_id"))
            return {"value": 0.0, "success": True, "failed_reason": None}

        inp = _make_input()
        preds = [
            PredictionRecord(
                id=f"pre-{mid}",
                input_id="inp-1",
                model_id=mid,
                prediction_config_id="CFG_1",
                scope_key="BTC-60",
                scope={"subject": "BTC"},
                status="PENDING",
                exec_time_ms=10.0,
                inference_output={"value": 0.5},
                performed_at=now - timedelta(minutes=5),
                resolvable_at=now - timedelta(minutes=1),
            )
            for mid in ["alpha", "beta"]
        ]

        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=capturing_scorer,
            feed_reader=FakeFeedReader(records=_make_feed_records()),
            input_repository=MemInputRepository([inp]),
            prediction_repository=MemPredictionRepository(preds),
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
        )

        service.run_once()

        self.assertEqual(sorted(captured), ["alpha", "beta"])

    def test_model_id_does_not_clobber_inference_output(self):
        """If inference_output already has a 'model_id' key (edge case),
        the prediction entity's model_id should take precedence."""
        captured = []

        def capturing_scorer(prediction, ground_truth):
            captured.append(dict(prediction))
            return {"value": 0.0, "success": True, "failed_reason": None}

        service = ScoreService(
            checkpoint_interval_seconds=60,
            scoring_function=capturing_scorer,
            feed_reader=FakeFeedReader(records=_make_feed_records()),
            input_repository=MemInputRepository([_make_input()]),
            prediction_repository=MemPredictionRepository(
                [
                    PredictionRecord(
                        id="pre-1",
                        input_id="inp-1",
                        model_id="correct_model",
                        prediction_config_id="CFG_1",
                        scope_key="BTC-60",
                        scope={"subject": "BTC"},
                        status="PENDING",
                        exec_time_ms=10.0,
                        inference_output={"value": 0.5, "model_id": "wrong_model"},
                        performed_at=now - timedelta(minutes=5),
                        resolvable_at=now - timedelta(minutes=1),
                    ),
                ]
            ),
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
        )

        service.run_once()

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["model_id"], "correct_model")


if __name__ == "__main__":
    unittest.main()
