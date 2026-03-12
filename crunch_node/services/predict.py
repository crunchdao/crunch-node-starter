"""Base predict service: get data, store predictions, resolve actuals."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from crunch_node.crunch_config import CrunchConfig
from crunch_node.db.repositories import (
    DBInputRepository,
    DBModelRepository,
    DBPredictionRepository,
)
from crunch_node.entities.model import Model
from crunch_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    PredictionStatus,
)
from crunch_node.services.feed_reader import FeedReader
from crunch_node.services.predict_components import (
    ModelConcurrentRunner,
    ModelRegistry,
    OutputValidator,
    PredictionKernel,
    PredictionRecordFactory,
)


class PredictService:
    """Shared predict primitives for concrete orchestrators.

    Subclasses own data ingestion/streaming and trigger/scheduling policy.
    This base class provides shared model invocation, validation, record
    construction, and persistence helpers.
    """

    def __init__(
        self,
        feed_reader: FeedReader | None = None,
        config: CrunchConfig | None = None,
        input_repository: DBInputRepository | None = None,
        model_repository: DBModelRepository | None = None,
        prediction_repository: DBPredictionRepository | None = None,
        runner: ModelConcurrentRunner | None = None,
        model_runner_node_host: str = "model-orchestrator",
        model_runner_node_port: int = 9091,
        model_runner_timeout_seconds: float = 60,
        crunch_id: str = "starter-challenge",
        base_classname: str = "cruncher.BaseClass",
        gateway_cert_dir: str | None = None,
        secure_cert_dir: str | None = None,
        **kwargs,
    ):
        self.feed_reader = feed_reader
        self.config = config or CrunchConfig()

        self.input_repository = input_repository
        self.model_repository = model_repository
        self.prediction_repository = prediction_repository
        self.crunch_id = crunch_id
        self.base_classname = base_classname

        self._runner = runner

        self._known_models: dict[str, Model] = {}
        self.logger = logging.getLogger(type(self).__name__)
        self._model_registry = ModelRegistry(
            known_models=self._known_models,
            model_repository=self.model_repository,
            logger=self.logger,
        )
        self._output_validator = OutputValidator(
            output_type=self.config.output_type,
            logger=self.logger,
        )
        self._record_factory = PredictionRecordFactory()
        self._kernel = PredictionKernel(
            runner=runner,
            model_runner_node_host=model_runner_node_host,
            model_runner_node_port=model_runner_node_port,
            model_runner_timeout_seconds=model_runner_timeout_seconds,
            crunch_id=crunch_id,
            base_classname=base_classname,
            gateway_cert_dir=gateway_cert_dir,
            secure_cert_dir=secure_cert_dir,
            logger=self.logger,
        )
        self.stop_event = asyncio.Event()

    # ── 1. get data ──

    def get_data(self, now: datetime) -> InputRecord:
        """Fetch input from feed reader, save to DB.

        The feed reader's normalizer validates and shapes the data.
        No additional validation is performed here.
        """
        if self.feed_reader is None:
            raise RuntimeError(
                "PredictService.get_data requires a feed_reader; "
                "non-feed modes should provide raw_input directly"
            )

        raw = self.feed_reader.get_input(now)
        feed_timing = raw.pop("_feed_timing", None)

        record = InputRecord(
            id=f"INP_{now.strftime('%Y%m%d_%H%M%S.%f')[:-3]}",
            raw_data=raw,
            received_at=now,
        )

        if feed_timing:
            record._timing.update(feed_timing)

        if self.input_repository is not None:
            self.input_repository.save(record)

        return record

    # ── 2. store predictions ──

    async def _call_models(self, scope: dict[str, Any]) -> dict:
        """Send call to model runner using the configured method name."""
        method = self.config.call_method.method
        args = self._kernel.encode_predict(
            scope=scope,
            call_args=self.config.call_method.args,
            scope_defaults=self.config.scope.model_dump(),
        )
        return await self._kernel.call(method, args)

    async def _feed_update_models(self, inference_input: dict[str, Any]) -> None:
        """Send latest data to all models."""
        responses = await self._kernel.call(
            "feed_update", self._kernel.encode_feed_update(inference_input)
        )
        for model_run, _ in responses.items():
            self.register_model(self._to_model(model_run))

    def _build_record(
        self,
        *,
        model_id: str,
        input_id: str,
        scope_key: str,
        scope: dict[str, Any],
        status: str,
        output: dict[str, Any],
        now: datetime,
        resolvable_at: datetime | None,
        exec_time_ms: float = 0.0,
        config_id: str | None = None,
        timing_data: dict[str, Any] | None = None,
    ) -> PredictionRecord:
        """Construct a PredictionRecord from model runner output."""
        factory = getattr(self, "_record_factory", None)
        if factory is None:
            factory = PredictionRecordFactory()
            self._record_factory = factory

        return factory.build(
            model_id=model_id,
            input_id=input_id,
            scope_key=scope_key,
            scope=scope,
            status=status,
            output=output,
            now=now,
            resolvable_at=resolvable_at,
            exec_time_ms=exec_time_ms,
            config_id=config_id,
            timing_data=timing_data,
        )

    def _save(self, predictions: list[PredictionRecord]) -> None:
        """Persist prediction records to the repository.

        This is a critical correctness write-path: score/report workers depend
        on these records. Foreign key integrity requires that all referenced
        models exist before predictions can be saved.
        """
        if not predictions:
            return

        # Ensure all referenced models exist in database before saving predictions
        # to prevent foreign key violations on predictions.model_id → models.id
        registry = getattr(self, "_model_registry", None)
        if registry is not None and registry._dirty_model_ids:
            self.logger.debug(
                f"Flushing {len(registry._dirty_model_ids)} dirty models before prediction save"
            )
            registry.flush_non_critical()

        # Critical path: save predictions with guaranteed FK integrity
        self.prediction_repository.save_all(predictions)
        self.logger.info("Saved %d predictions", len(predictions))

        if self.logger.isEnabledFor(logging.DEBUG):
            for p in predictions:
                out = p.inference_output or {}
                summary = {
                    k: round(v, 6) if isinstance(v, float) else v
                    for k, v in list(out.items())[:3]
                }
                self.logger.debug(
                    "  model=%s scope=%s status=%s output=%s",
                    p.model_id,
                    p.scope_key,
                    p.status,
                    summary,
                )

    # ── runner lifecycle ──

    async def init_runner(self) -> None:
        # Compatibility: some tests/flows swap ``self._runner`` directly.
        # Keep kernel and service runner references aligned.
        if self._runner is not self._kernel.runner:
            await self._kernel.replace_runner(self._runner)

        await self._kernel.init_runner()
        self._runner = self._kernel.runner

    async def shutdown(self) -> None:
        self.stop_event.set()
        await self._kernel.shutdown()

    # ── model management ──

    def register_model(self, model: Model) -> None:
        registry = getattr(self, "_model_registry", None)
        if registry is None:
            known = getattr(self, "_known_models", {})
            self._known_models = known
            registry = ModelRegistry(
                known_models=known,
                model_repository=getattr(self, "model_repository", None),
                logger=getattr(self, "logger", None),
            )
            self._model_registry = registry

        registry.register(model)

    def validate_output(self, output: dict[str, Any]) -> str | None:
        """Validate model output against InferenceOutput schema.

        Returns None if valid, or an error string if invalid.
        Catches both type mismatches AND outputs where no keys match
        the schema (model returning the wrong format entirely).
        """
        validator = getattr(self, "_output_validator", None)
        if validator is None:
            validator = OutputValidator(
                output_type=self.config.output_type,
                logger=getattr(self, "logger", None),
            )
            self._output_validator = validator

        return validator.validate_and_normalize(output)

    def _map_runner_result(
        self, result: Any
    ) -> tuple[PredictionStatus, dict[str, Any]]:
        """Map runner response to (PredictionStatus, normalized_output)."""
        raw_status = getattr(result, "status", "UNKNOWN")
        runner_status = (
            str(raw_status.value) if hasattr(raw_status, "value") else str(raw_status)
        )

        output = getattr(result, "result", {})
        output = output if isinstance(output, dict) else {"result": output}

        validation_error = self.validate_output(output)
        if validation_error:
            return PredictionStatus.FAILED, {
                "_validation_error": validation_error,
                "raw_output": output,
            }

        if runner_status == "SUCCESS":
            return PredictionStatus.PENDING, output

        if runner_status in PredictionStatus.__members__:
            return PredictionStatus(runner_status), output

        return PredictionStatus.FAILED, output

    @staticmethod
    def _to_model(model_run) -> Model:
        infos = getattr(model_run, "infos", {}) or {}
        return Model(
            id=str(model_run.model_id),
            name=str(getattr(model_run, "model_name", "unknown-model")),
            player_id=str(infos.get("cruncher_id", "unknown-player")),
            player_name=str(infos.get("cruncher_name", "Unknown")),
            deployment_identifier=str(
                getattr(model_run, "deployment_id", "unknown-deployment")
            ),
        )

    # ── proto encoding compatibility wrappers ──

    def _encode_feed_update(self, inference_input: dict[str, Any]) -> tuple:
        return self._kernel.encode_feed_update(inference_input)

    @classmethod
    def _get_variant_type(cls, type_name: str) -> Any:
        return PredictionKernel.get_variant_type(type_name)

    def _encode_predict(self, scope: dict[str, Any]) -> tuple:
        return self._kernel.encode_predict(
            scope=scope,
            call_args=self.config.call_method.args,
            scope_defaults=self.config.scope.model_dump(),
        )
