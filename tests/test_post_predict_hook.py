"""Tests for pre_feed_update_hook and post_predict_hook on RealtimePredictService."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.prediction import InputRecord, PredictionRecord
from crunch_node.services.realtime_predict import (
    RealtimePredictService,
    RealtimeServiceConfig,
)

# ── reuse test fixtures from test_node_template_predict_service ──


class FakeModelRun:
    def __init__(self, model_id, model_name="model-1", deployment_id="dep-1"):
        self.model_id = model_id
        self.model_name = model_name
        self.deployment_id = deployment_id
        self.infos = {"cruncher_id": "p1", "cruncher_name": "alice"}


class FakePredictionResult:
    def __init__(self, result=None, status="SUCCESS", exec_time_us=100):
        self.result = result or {"value": 0.5}
        self.status = status
        self.exec_time_us = exec_time_us


class FakeRunner:
    def __init__(self):
        self._initialized = False

    async def init(self):
        self._initialized = True

    async def sync(self):
        pass

    async def call(self, method, args):
        return {FakeModelRun("m1"): FakePredictionResult()}


class FakeFeedReader:
    def __init__(self):
        self.source = "pyth"
        self.subject = "BTC"
        self.kind = "tick"
        self.granularity = "1s"

    def get_input(self, now):
        return {}


class InMemoryModelRepository:
    def __init__(self):
        self.models = {}

    def save(self, model):
        self.models[model.id] = model


class InMemoryPredictionRepository:
    def __init__(self):
        self.saved_predictions: list[PredictionRecord] = []

    def save_all(self, predictions):
        self.saved_predictions.extend(list(predictions))

    def fetch_active_configs(self):
        return [
            {
                "id": "CFG_1",
                "scope_key": "BTC-60-60",
                "scope_template": {"subject": "BTC", "horizon": 60, "step": 60},
                "schedule": {
                    "prediction_interval_seconds": 60,
                    "resolve_horizon_seconds": 60,
                },
                "active": True,
                "order": 1,
            }
        ]


class InMemoryInputRepository:
    def __init__(self):
        self.records: list[InputRecord] = []

    def save(self, record: InputRecord):
        self.records.append(record)


class HookedCrunchConfig(CrunchConfig):
    """CrunchConfig subclass with realtime_service (as a pack would define)."""

    realtime_service: RealtimeServiceConfig = Field(
        default_factory=RealtimeServiceConfig
    )


def _make_service(pre_feed_update_hook=None, post_predict_hook=None):
    return RealtimePredictService(
        checkpoint_interval_seconds=60,
        pre_feed_update_hook=pre_feed_update_hook,
        post_predict_hook=post_predict_hook,
        feed_reader=FakeFeedReader(),
        config=CrunchConfig(),
        input_repository=InMemoryInputRepository(),
        model_repository=InMemoryModelRepository(),
        prediction_repository=InMemoryPredictionRepository(),
        runner=FakeRunner(),
    )


class TestPreFeedUpdateHook(unittest.IsolatedAsyncioTestCase):
    async def test_hook_is_called_with_correct_args(self):
        captured: dict[str, Any] = {}

        def hook(input_record, now):
            captured["input_record"] = input_record
            captured["now"] = now
            return input_record

        service = _make_service(pre_feed_update_hook=hook)
        now = datetime.now(UTC)
        await service.process_tick(raw_input={"symbol": "BTC"}, now=now)

        self.assertIsInstance(captured["input_record"], InputRecord)
        self.assertEqual(captured["now"], now)

    async def test_hook_can_mutate_input_record(self):
        def hook(input_record, now):
            input_record.raw_data["mutated"] = True
            return input_record

        captured: dict[str, Any] = {}

        def post_hook(predictions, input_record, now):
            captured["raw_data"] = input_record.raw_data
            return predictions

        service = _make_service(
            pre_feed_update_hook=hook,
            post_predict_hook=post_hook,
        )
        await service.process_tick(raw_input={"symbol": "BTC"}, now=datetime.now(UTC))

        self.assertTrue(captured["raw_data"].get("mutated"))


class TestPostPredictHook(unittest.IsolatedAsyncioTestCase):
    async def test_hook_is_called_with_correct_args(self):
        """Hook receives predictions, input_record, and now."""
        captured: dict[str, Any] = {}

        def hook(predictions, input_record, now):
            captured["predictions"] = predictions
            captured["input_record"] = input_record
            captured["now"] = now
            return predictions

        service = _make_service(post_predict_hook=hook)
        now = datetime.now(UTC)
        await service.process_tick(raw_input={"symbol": "BTC"}, now=now)

        self.assertIn("predictions", captured)
        self.assertIsInstance(captured["predictions"], list)
        self.assertGreater(len(captured["predictions"]), 0)
        self.assertIsInstance(captured["predictions"][0], PredictionRecord)
        self.assertIsInstance(captured["input_record"], InputRecord)
        self.assertEqual(captured["now"], now)

    async def test_hook_can_mutate_predictions(self):
        """Hook can modify inference_output before save."""

        def hook(predictions, input_record, now):
            for p in predictions:
                p.inference_output["hook_added"] = True
            return predictions

        service = _make_service(post_predict_hook=hook)
        await service.process_tick(raw_input={"symbol": "BTC"}, now=datetime.now(UTC))

        repo = service.prediction_repository
        self.assertGreater(len(repo.saved_predictions), 0)
        for pred in repo.saved_predictions:
            self.assertTrue(pred.inference_output.get("hook_added"))

    async def test_hook_can_filter_predictions(self):
        """Hook can remove predictions from the list."""

        def hook(predictions, input_record, now):
            return []  # drop all

        service = _make_service(post_predict_hook=hook)
        await service.process_tick(raw_input={"symbol": "BTC"}, now=datetime.now(UTC))

        repo = service.prediction_repository
        self.assertEqual(len(repo.saved_predictions), 0)

    async def test_no_hook_saves_normally(self):
        """Without a hook, predictions are saved as usual."""
        service = _make_service(post_predict_hook=None)
        await service.process_tick(raw_input={"symbol": "BTC"}, now=datetime.now(UTC))

        repo = service.prediction_repository
        self.assertGreater(len(repo.saved_predictions), 0)

    async def test_hook_receives_input_record_with_raw_data(self):
        """Hook's input_record contains the raw feed data."""
        captured: dict[str, Any] = {}

        def hook(predictions, input_record, now):
            captured["raw_data"] = input_record.raw_data
            return predictions

        service = _make_service(post_predict_hook=hook)
        await service.process_tick(
            raw_input={"symbol": "BTC", "price": 42000.0},
            now=datetime.now(UTC),
        )

        self.assertEqual(captured["raw_data"].get("symbol"), "BTC")
        self.assertEqual(captured["raw_data"].get("price"), 42000.0)


# ── CrunchConfig integration ──


class TestCrunchConfigHooks(unittest.TestCase):
    def test_base_config_has_no_realtime_service(self):
        config = CrunchConfig()
        self.assertFalse(hasattr(config, "realtime_service"))

    def test_hooked_config_defaults_are_none(self):
        config = HookedCrunchConfig()
        self.assertIsNone(config.realtime_service.pre_feed_update_hook)
        self.assertIsNone(config.realtime_service.post_predict_hook)

    def test_accepts_pre_feed_update_callable(self):
        def my_hook(input_record, now):
            return input_record

        config = HookedCrunchConfig(
            realtime_service=RealtimeServiceConfig(pre_feed_update_hook=my_hook)
        )
        self.assertIs(config.realtime_service.pre_feed_update_hook, my_hook)

    def test_accepts_post_predict_callable(self):
        def my_hook(predictions, input_record, now):
            return predictions

        config = HookedCrunchConfig(
            realtime_service=RealtimeServiceConfig(post_predict_hook=my_hook)
        )
        self.assertIs(config.realtime_service.post_predict_hook, my_hook)


# ── predict_worker wiring ──


class TestPredictWorkerWiring(unittest.TestCase):
    def test_hooks_passed_from_config_to_service(self):
        """build_service wires config hooks to service."""
        from unittest.mock import MagicMock, patch

        def my_pre_hook(input_record, now):
            return input_record

        def my_post_hook(predictions, input_record, now):
            return predictions

        config = HookedCrunchConfig(
            realtime_service=RealtimeServiceConfig(
                pre_feed_update_hook=my_pre_hook,
                post_predict_hook=my_post_hook,
            )
        )

        mock_settings = MagicMock()
        mock_settings.model_runner_node_host = "localhost"
        mock_settings.model_runner_node_port = 9091
        mock_settings.model_runner_timeout_seconds = 60
        mock_settings.crunch_id = "test"
        mock_settings.base_classname = "cruncher.BaseModelClass"
        mock_settings.gateway_cert_dir = None
        mock_settings.secure_cert_dir = None
        mock_settings.checkpoint_interval_seconds = 3600

        with (
            patch(
                "crunch_node.workers.predict_worker.RuntimeSettings.from_env",
                return_value=mock_settings,
            ),
            patch(
                "crunch_node.workers.predict_worker.load_config",
                return_value=config,
            ),
            patch(
                "crunch_node.workers.predict_worker.create_session",
                return_value=MagicMock(),
            ),
            patch(
                "crunch_node.workers.predict_worker.FeedReader.from_env",
                return_value=FakeFeedReader(),
            ),
            patch(
                "crunch_node.workers.predict_worker.DBInputRepository",
                return_value=MagicMock(),
            ),
            patch(
                "crunch_node.workers.predict_worker.DBModelRepository",
                return_value=MagicMock(),
            ),
            patch(
                "crunch_node.workers.predict_worker.DBPredictionRepository",
                return_value=MagicMock(),
            ),
        ):
            from crunch_node.workers.predict_worker import build_service

            service = build_service()

        self.assertIsInstance(service, RealtimePredictService)
        self.assertIs(service.pre_feed_update_hook, my_pre_hook)
        self.assertIs(service.post_predict_hook, my_post_hook)


if __name__ == "__main__":
    unittest.main()
