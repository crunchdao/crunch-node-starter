import unittest
from datetime import UTC, datetime

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.model import Model
from crunch_node.entities.prediction import InputRecord, PredictionRecord
from crunch_node.services.realtime_predict import RealtimePredictService


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
    def __init__(self, payload=None):
        self._payload = payload or {}
        self.source = "pyth"
        self.subject = "BTC"
        self.kind = "tick"
        self.granularity = "1s"

    def get_input(self, now):
        return self._payload

    def get_ground_truth(self, performed_at, resolvable_at, asset=None):
        return None


class InMemoryModelRepository:
    def __init__(self):
        self.models: dict[str, Model] = {}

    def fetch_all(self):
        return self.models

    def save(self, model: Model):
        self.models[model.id] = model

    def save_all(self, models):
        for model in models:
            self.save(model)


class InMemoryPredictionRepository:
    def __init__(self):
        self.saved_predictions: list[PredictionRecord] = []

    def save_prediction(self, prediction: PredictionRecord):
        self.saved_predictions.append(prediction)

    def save_predictions(self, predictions):
        self.saved_predictions.extend(list(predictions))

    def save_actuals(self, prediction_id, actuals):
        for p in self.saved_predictions:
            if p.id == prediction_id:
                p.actuals = actuals
                p.status = "RESOLVED"

    def find_predictions(self, *, status=None, resolvable_before=None, **kwargs):
        results = self.saved_predictions
        if status is not None:
            if isinstance(status, list):
                results = [p for p in results if p.status in status]
            else:
                results = [p for p in results if p.status == status]
        return results

    # legacy compat
    def save(self, prediction):
        self.save_prediction(prediction)

    def save_all(self, predictions):
        self.save_predictions(predictions)

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
        for i, r in enumerate(self.records):
            if r.id == record.id:
                self.records[i] = record
                return
        self.records.append(record)

    def get(self, input_id: str):
        return next((r for r in self.records if r.id == input_id), None)

    def find(self, **kwargs):
        return list(self.records)


class NoConfigPredictionRepository(InMemoryPredictionRepository):
    def fetch_active_configs(self):
        return []


def _make_service(
    feed_reader=None, prediction_repo=None, input_repo=None, runner=None, config=None
):
    return RealtimePredictService(
        checkpoint_interval_seconds=60,
        feed_reader=feed_reader or FakeFeedReader(),
        config=config or CrunchConfig(),
        input_repository=input_repo,
        model_repository=InMemoryModelRepository(),
        prediction_repository=prediction_repo or InMemoryPredictionRepository(),
        runner=runner or FakeRunner(),
    )


class TestRealtimePredictService(unittest.IsolatedAsyncioTestCase):
    async def test_process_tick_generates_prediction_rows(self):
        repo = InMemoryPredictionRepository()
        service = _make_service(prediction_repo=repo)

        await service.process_tick(
            raw_input={"symbol": "BTC", "asof_ts": 123}, now=datetime.now(UTC)
        )

        self.assertIn("m1", service._known_models)
        self.assertGreaterEqual(len(repo.saved_predictions), 1)

        pred = repo.saved_predictions[0]
        self.assertEqual(pred.scope_key, "BTC-60-60")
        self.assertEqual(pred.scope.get("subject"), "BTC")
        self.assertIsNotNone(pred.input_id)
        self.assertIn("value", pred.inference_output)

    async def test_process_tick_uses_feed_reader_when_no_raw_input(self):
        repo = InMemoryPredictionRepository()
        service = _make_service(
            feed_reader=FakeFeedReader({"symbol": "ETH", "asof_ts": 999}),
            prediction_repo=repo,
        )

        await service.process_tick(now=datetime.now(UTC))

        self.assertGreaterEqual(len(repo.saved_predictions), 1)
        self.assertIsNotNone(repo.saved_predictions[0].input_id)

    async def test_process_tick_returns_false_when_no_active_configs(self):
        service = _make_service(prediction_repo=NoConfigPredictionRepository())

        with self.assertLogs("RealtimePredictService", level="INFO") as logs:
            changed = await service.process_tick(
                raw_input={"symbol": "BTC"}, now=datetime.now(UTC)
            )

        self.assertFalse(changed)
        self.assertTrue(
            any("No active prediction configs" in line for line in logs.output)
        )

    async def test_process_tick_marks_failed_on_output_validation_error(self):
        from pydantic import BaseModel, Field

        class StrictOutput(BaseModel):
            value: float = Field(ge=0.0, le=1.0)

        class BadRunner(FakeRunner):
            async def call(self, method, args):
                return {
                    FakeModelRun("m1"): FakePredictionResult(
                        result={"value": "not-a-number"}
                    )
                }

        repo = InMemoryPredictionRepository()
        service = _make_service(
            prediction_repo=repo,
            runner=BadRunner(),
            config=CrunchConfig(output_type=StrictOutput),
        )

        with self.assertLogs("RealtimePredictService", level="ERROR") as logs:
            changed = await service.process_tick(
                raw_input={"symbol": "BTC"}, now=datetime.now(UTC)
            )

        self.assertTrue(changed)
        pred = repo.saved_predictions[0]
        self.assertEqual(pred.status, "FAILED")
        self.assertIn("_validation_error", pred.inference_output)
        self.assertTrue(
            any("INFERENCE_OUTPUT_VALIDATION_ERROR" in line for line in logs.output)
        )

    async def test_process_tick_sets_prediction_scope_with_feed_dimensions(self):
        """Prediction scope must include source/subject/kind/granularity
        so the score worker can query matching feed records for ground truth."""
        input_repo = InMemoryInputRepository()
        pred_repo = InMemoryPredictionRepository()
        feed_reader = FakeFeedReader({"symbol": "BTC"})
        feed_reader.source = "binance"
        feed_reader.subject = "BTC"
        feed_reader.kind = "candle"
        feed_reader.granularity = "1m"

        service = _make_service(
            feed_reader=feed_reader,
            prediction_repo=pred_repo,
            input_repo=input_repo,
        )

        await service.process_tick(raw_input={"symbol": "BTC"}, now=datetime.now(UTC))

        self.assertGreater(len(pred_repo.saved_predictions), 0)
        pred = pred_repo.saved_predictions[0]
        # Feed dimensions must be in scope for score worker to query feed records
        self.assertEqual(pred.scope.get("source"), "binance")
        self.assertEqual(pred.scope.get("kind"), "candle")
        self.assertEqual(pred.scope.get("granularity"), "1m")
        self.assertIn("subject", pred.scope)

    async def test_process_tick_sets_prediction_resolvable_at(self):
        """Predictions must have resolvable_at so score worker can find
        predictions ready for ground truth resolution."""
        input_repo = InMemoryInputRepository()
        pred_repo = InMemoryPredictionRepository()
        service = _make_service(
            prediction_repo=pred_repo,
            input_repo=input_repo,
        )

        now = datetime.now(UTC)
        await service.process_tick(raw_input={"symbol": "BTC"}, now=now)

        self.assertGreater(len(pred_repo.saved_predictions), 0)
        for pred in pred_repo.saved_predictions:
            self.assertIsNotNone(pred.resolvable_at)
            self.assertGreaterEqual(pred.resolvable_at, now)

    async def test_custom_call_method_uses_configured_method_name(self):
        """Finding F: CallMethodConfig controls which gRPC method is called."""
        from crunch_node.crunch_config import CallMethodArg, CallMethodConfig

        class CapturingRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.captured_method = None
                self.captured_args = None

            async def call(self, method, args):
                self.captured_method = method
                self.captured_args = args
                return {FakeModelRun("m1"): FakePredictionResult()}

        runner = CapturingRunner()
        config = CrunchConfig(
            call_method=CallMethodConfig(
                method="trade",
                args=[
                    CallMethodArg(name="symbol", type="STRING"),
                    CallMethodArg(name="side", type="STRING"),
                ],
            ),
        )
        repo = InMemoryPredictionRepository()
        # Provide scope_template values that match the custom args
        repo.fetch_active_configs = lambda: [
            {
                "id": "CFG_T",
                "scope_key": "BTC-trade",
                "scope_template": {"symbol": "BTCUSDT", "side": "LONG"},
                "schedule": {
                    "prediction_interval_seconds": 60,
                    "resolve_horizon_seconds": 60,
                },
                "active": True,
                "order": 1,
            }
        ]
        service = _make_service(
            prediction_repo=repo,
            runner=runner,
            config=config,
        )

        await service.process_tick(raw_input={"symbol": "BTC"}, now=datetime.now(UTC))

        self.assertEqual(runner.captured_method, "trade")
        self.assertGreaterEqual(len(repo.saved_predictions), 1)

    async def test_default_call_method_is_predict(self):
        """Default CallMethodConfig calls 'predict' with (subject, horizon, step)."""

        config = CrunchConfig()
        self.assertEqual(config.call_method.method, "predict")
        self.assertEqual(len(config.call_method.args), 3)
        self.assertEqual(config.call_method.args[0].name, "subject")
        self.assertEqual(config.call_method.args[1].name, "resolve_horizon_seconds")
        self.assertEqual(config.call_method.args[2].name, "step_seconds")


if __name__ == "__main__":
    unittest.main()
