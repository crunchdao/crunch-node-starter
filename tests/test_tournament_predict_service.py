"""Tests for TournamentPredictService — round-based batch inference + scoring."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import BaseModel

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.prediction import (
    PredictionRecord,
    PredictionStatus,
    ScoreRecord,
)
from crunch_node.services.predict import PredictService
from crunch_node.services.tournament_predict import TournamentPredictService
from crunch_node.workers.predict_worker import _resolve_service_class

# ── fixtures ──


class FakeRunner:
    async def init(self):
        pass

    async def sync(self):
        pass

    async def call(self, method, args):
        return {}


class FakeRepo:
    """In-memory repository stand-in."""

    def __init__(self):
        self.items: dict[str, Any] = {}

    def save(self, item):
        self.items[item.id] = item

    def save_all(self, items):
        for item in items:
            self.save(item)

    def get(self, item_id):
        return self.items.get(item_id)

    def find(self, **kwargs):
        results = list(self.items.values())
        scope_key = kwargs.get("scope_key")
        scope_key_prefix = kwargs.get("scope_key_prefix")
        status = kwargs.get("status")
        if scope_key:
            results = [r for r in results if getattr(r, "scope_key", None) == scope_key]
        if scope_key_prefix:
            results = [
                r
                for r in results
                if (getattr(r, "scope_key", None) or "").startswith(scope_key_prefix)
            ]
        if status:
            if isinstance(status, list):
                results = [r for r in results if getattr(r, "status", None) in status]
            else:
                results = [r for r in results if getattr(r, "status", None) == status]
        return results

    def fetch_all(self):
        return self.items

    def fetch_active_configs(self):
        return []

    def rollback(self):
        pass


def make_service(scoring_function=None, config=None) -> TournamentPredictService:
    """Create a TournamentPredictService with in-memory repos."""
    return TournamentPredictService(
        config=config or CrunchConfig(),
        input_repository=FakeRepo(),
        model_repository=FakeRepo(),
        prediction_repository=FakeRepo(),
        score_repository=FakeRepo(),
        scoring_function=scoring_function,
        runner=FakeRunner(),
    )


# ── class hierarchy ──


class TestTournamentPredictServiceHierarchy(unittest.TestCase):
    def test_is_predict_service_subclass(self):
        self.assertTrue(issubclass(TournamentPredictService, PredictService))

    def test_resolve_service_class(self):
        """predict_worker resolves TournamentPredictService correctly."""
        config = CrunchConfig(predict_service_class=TournamentPredictService)
        cls = _resolve_service_class(config)
        self.assertIs(cls, TournamentPredictService)

    def test_instantiation_without_feed_reader(self):
        """TournamentPredictService works without a feed_reader."""
        service = make_service()
        self.assertIsInstance(service, TournamentPredictService)
        self.assertFalse(hasattr(service, "feed_reader"))


# ── run() is a no-op ──


class TestTournamentRun(unittest.TestCase):
    def test_run_waits_for_shutdown(self):
        """run() blocks until stop_event is set."""
        service = make_service()

        async def _test():
            # Set stop event after a short delay
            async def _stop():
                await asyncio.sleep(0.05)
                service.stop_event.set()

            stop_task = asyncio.create_task(_stop())
            await service.run()
            await stop_task

        asyncio.run(_test())


# ── run_inference ──


class TestRunInference(unittest.TestCase):
    def test_run_inference_saves_input_and_predictions(self):
        """run_inference creates InputRecord and PredictionRecords."""
        service = make_service()
        now = datetime(2026, 3, 1, 18, 0, 0, tzinfo=UTC)
        features = [{"value": 1.0}, {"value": 2.0}]

        async def _test():
            predictions = await service.run_inference("round-001", features, now=now)
            return predictions

        predictions = asyncio.run(_test())

        # No models registered with FakeRunner → 0 predictions (no absent models either)
        self.assertEqual(len(predictions), 0)

        # But input record should be saved
        self.assertEqual(len(service.input_repository.items), 1)
        inp = list(service.input_repository.items.values())[0]
        self.assertEqual(inp.raw_data["round_id"], "round-001")
        self.assertEqual(len(inp.raw_data["features"]), 2)

    def test_run_inference_validates_features(self):
        """Features are validated through input_type."""

        class StrictInput(BaseModel):
            x: float
            y: float

        config = CrunchConfig(input_type=StrictInput)
        service = make_service(config=config)

        async def _test():
            # Missing 'y' field should raise
            with self.assertRaises(Exception):
                await service.run_inference("round-bad", [{"x": 1.0}])

        asyncio.run(_test())


# ── score_round ──


class TestScoreRound(unittest.TestCase):
    def test_score_round_scores_pending_predictions(self):
        """score_round scores all PENDING predictions for a round."""

        def scoring_fn(prediction, ground_truth):
            return {"value": 1.0, "success": True, "failed_reason": None}

        service = make_service(scoring_function=scoring_fn)

        # Manually insert a prediction
        pred = PredictionRecord(
            id="PRE_model1_round-001",
            input_id="INP_round-001",
            model_id="model1",
            prediction_config_id=None,
            scope_key="round-001",
            scope={"round_id": "round-001"},
            status=PredictionStatus.PENDING,
            exec_time_ms=0.0,
            inference_output={"value": 42.0},
        )
        service.prediction_repository.save(pred)

        now = datetime(2026, 3, 2, 18, 0, 0, tzinfo=UTC)
        scores = service.score_round("round-001", {"value": 42.0}, now=now)

        self.assertEqual(len(scores), 1)
        self.assertTrue(scores[0].success)
        self.assertEqual(scores[0].result["value"], 1.0)

        # Prediction should be marked SCORED
        updated = service.prediction_repository.get(pred.id)
        self.assertEqual(updated.status, PredictionStatus.SCORED)

    def test_score_round_no_scoring_function_raises(self):
        """score_round raises if no scoring_function is configured."""
        service = make_service()  # No scoring function

        pred = PredictionRecord(
            id="PRE_model1_round-001",
            input_id="INP_round-001",
            model_id="model1",
            prediction_config_id=None,
            scope_key="round-001",
            scope={},
            status=PredictionStatus.PENDING,
            exec_time_ms=0.0,
        )
        service.prediction_repository.save(pred)

        with self.assertRaises(RuntimeError) as ctx:
            service.score_round("round-001", {"value": 1.0})
        self.assertIn("scoring_function", str(ctx.exception))

    def test_score_round_empty_round(self):
        """score_round returns empty list for nonexistent round."""

        def scoring_fn(prediction, ground_truth):
            return {"value": 0.0, "success": True, "failed_reason": None}

        service = make_service(scoring_function=scoring_fn)
        scores = service.score_round("nonexistent", {"value": 1.0})
        self.assertEqual(len(scores), 0)

    def test_score_round_handles_scoring_errors(self):
        """score_round catches scoring errors and marks score as failed."""

        def bad_scoring_fn(prediction, ground_truth):
            raise ValueError("something broke")

        service = make_service(scoring_function=bad_scoring_fn)

        pred = PredictionRecord(
            id="PRE_model1_round-err",
            input_id="INP_round-err",
            model_id="model1",
            prediction_config_id=None,
            scope_key="round-err",
            scope={},
            status=PredictionStatus.PENDING,
            exec_time_ms=0.0,
        )
        service.prediction_repository.save(pred)

        scores = service.score_round("round-err", {"value": 1.0})
        self.assertEqual(len(scores), 1)
        self.assertFalse(scores[0].success)
        self.assertIn("something broke", scores[0].failed_reason)

    def test_score_round_with_list_ground_truth(self):
        """score_round with list GT scores per-sample predictions 1:1."""
        received_gts: list[dict] = []

        def scoring_fn(prediction, ground_truth):
            received_gts.append(ground_truth)
            return {"value": 0.5, "success": True, "failed_reason": None}

        service = make_service(scoring_function=scoring_fn)

        # Two per-sample predictions (as created by run_inference)
        for idx in range(2):
            pred = PredictionRecord(
                id=f"PRE_model1_round-list_{idx}",
                input_id="INP_round-list",
                model_id="model1",
                prediction_config_id=None,
                scope_key=f"round-list:{idx}",
                scope={"round_id": "round-list", "feature_index": idx},
                status=PredictionStatus.PENDING,
                exec_time_ms=0.0,
            )
            service.prediction_repository.save(pred)

        gt_list = [{"value": 1.0}, {"value": 2.0}]
        scores = service.score_round("round-list", gt_list)
        # One score per sample
        self.assertEqual(len(scores), 2)
        # Each GT item passed individually to scoring function
        self.assertEqual(len(received_gts), 2)
        self.assertEqual(received_gts[0].value, 1.0)
        self.assertEqual(received_gts[1].value, 2.0)

    def test_score_round_validates_ground_truth(self):
        """Ground truth is validated through ground_truth_type."""

        class StrictGT(BaseModel):
            price: float

        def scoring_fn(prediction, ground_truth):
            return {"value": 0.0, "success": True, "failed_reason": None}

        config = CrunchConfig(ground_truth_type=StrictGT)
        service = make_service(scoring_function=scoring_fn, config=config)

        pred = PredictionRecord(
            id="PRE_model1_round-gt",
            input_id="INP_round-gt",
            model_id="model1",
            prediction_config_id=None,
            scope_key="round-gt",
            scope={},
            status=PredictionStatus.PENDING,
            exec_time_ms=0.0,
        )
        service.prediction_repository.save(pred)

        # Missing 'price' field
        with self.assertRaises(Exception):
            service.score_round("round-gt", {"wrong_field": 1.0})

    def test_score_round_only_scores_pending(self):
        """score_round skips already-scored predictions."""

        def scoring_fn(prediction, ground_truth):
            return {"value": 1.0, "success": True, "failed_reason": None}

        service = make_service(scoring_function=scoring_fn)

        # One pending, one already scored
        pred1 = PredictionRecord(
            id="PRE_model1_round-mix",
            input_id="INP_round-mix",
            model_id="model1",
            prediction_config_id=None,
            scope_key="round-mix",
            scope={},
            status=PredictionStatus.PENDING,
            exec_time_ms=0.0,
        )
        pred2 = PredictionRecord(
            id="PRE_model2_round-mix",
            input_id="INP_round-mix",
            model_id="model2",
            prediction_config_id=None,
            scope_key="round-mix",
            scope={},
            status=PredictionStatus.SCORED,
            exec_time_ms=0.0,
        )
        service.prediction_repository.save(pred1)
        service.prediction_repository.save(pred2)

        scores = service.score_round("round-mix", {"value": 1.0})
        self.assertEqual(len(scores), 1)  # Only pred1 scored


# ── round queries ──


class TestRoundQueries(unittest.TestCase):
    def test_get_round_status_not_found(self):
        service = make_service()
        status = service.get_round_status("nonexistent")
        self.assertEqual(status["status"], "not_found")

    def test_get_round_status_inference_complete(self):
        service = make_service()

        # Per-sample scope_key format
        pred = PredictionRecord(
            id="PRE_model1_round-q_0",
            input_id="INP_round-q",
            model_id="model1",
            prediction_config_id=None,
            scope_key="round-q:0",
            scope={"round_id": "round-q", "feature_index": 0},
            status=PredictionStatus.PENDING,
            exec_time_ms=0.0,
        )
        service.prediction_repository.save(pred)

        status = service.get_round_status("round-q")
        self.assertEqual(status["status"], "inference_complete")
        self.assertEqual(status["total"], 1)

    def test_get_round_status_scored(self):
        service = make_service()

        pred = PredictionRecord(
            id="PRE_model1_round-s_0",
            input_id="INP_round-s",
            model_id="model1",
            prediction_config_id=None,
            scope_key="round-s:0",
            scope={"round_id": "round-s", "feature_index": 0},
            status=PredictionStatus.SCORED,
            exec_time_ms=0.0,
        )
        service.prediction_repository.save(pred)

        status = service.get_round_status("round-s")
        self.assertEqual(status["status"], "scored")

    def test_get_round_predictions(self):
        service = make_service()

        pred = PredictionRecord(
            id="PRE_model1_round-gp_0",
            input_id="INP_round-gp",
            model_id="model1",
            prediction_config_id=None,
            scope_key="round-gp:0",
            scope={"round_id": "round-gp", "feature_index": 0},
            status=PredictionStatus.PENDING,
            exec_time_ms=0.0,
        )
        service.prediction_repository.save(pred)

        preds = service.get_round_predictions("round-gp")
        self.assertEqual(len(preds), 1)

        preds_filtered = service.get_round_predictions(
            "round-gp", status=PredictionStatus.SCORED
        )
        self.assertEqual(len(preds_filtered), 0)


# ── scaffold usage pattern ──


class TestScaffoldPattern(unittest.TestCase):
    def test_scaffold_config_sets_tournament_service(self):
        """Scaffold CrunchConfig sets predict_service_class = TournamentPredictService."""

        class TournamentConfig(CrunchConfig):
            predict_service_class: type | None = TournamentPredictService

        config = TournamentConfig()
        cls = _resolve_service_class(config)
        self.assertIs(cls, TournamentPredictService)

    def test_tournament_service_with_custom_types(self):
        """Tournament service works with custom Pydantic types."""

        class PropertyFeatures(BaseModel):
            sqft: float = 0.0
            bedrooms: int = 0

        class PriceGT(BaseModel):
            price: float = 0.0

        class PriceOutput(BaseModel):
            predicted_price: float = 0.0

        class PriceScore(BaseModel):
            value: float = 0.0
            mape: float = 0.0
            success: bool = True
            failed_reason: str | None = None

        config = CrunchConfig(
            input_type=PropertyFeatures,
            ground_truth_type=PriceGT,
            output_type=PriceOutput,
            score_type=PriceScore,
            predict_service_class=TournamentPredictService,
        )

        def scoring_fn(prediction, ground_truth):
            pred_price = getattr(prediction, "predicted_price", 0)
            actual_price = getattr(ground_truth, "price", 1)
            mape = abs(pred_price - actual_price) / max(abs(actual_price), 1e-9)
            return {
                "value": max(0.0, 1.0 - mape),
                "mape": mape,
                "success": True,
                "failed_reason": None,
            }

        service = TournamentPredictService(
            config=config,
            input_repository=FakeRepo(),
            model_repository=FakeRepo(),
            prediction_repository=FakeRepo(),
            score_repository=FakeRepo(),
            scoring_function=scoring_fn,
            runner=FakeRunner(),
        )

        # Simulate a scored prediction
        pred = PredictionRecord(
            id="PRE_model1_round-prop",
            input_id="INP_round-prop",
            model_id="model1",
            prediction_config_id=None,
            scope_key="round-prop",
            scope={},
            status=PredictionStatus.PENDING,
            exec_time_ms=0.0,
            inference_output={"predicted_price": 450000.0},
        )
        service.prediction_repository.save(pred)

        scores = service.score_round("round-prop", {"price": 500000.0})
        self.assertEqual(len(scores), 1)
        self.assertTrue(scores[0].success)
        self.assertAlmostEqual(scores[0].result["mape"], 0.1, places=5)
        self.assertAlmostEqual(scores[0].result["value"], 0.9, places=5)


if __name__ == "__main__":
    unittest.main()
