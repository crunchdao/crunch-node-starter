"""Base predict service: get data, store predictions, resolve actuals."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from coordinator_node.crunch_config import CrunchConfig
from coordinator_node.db.repositories import (
    DBInputRepository,
    DBModelRepository,
    DBPredictionRepository,
)
from coordinator_node.entities.model import Model
from coordinator_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    PredictionStatus,
)
from coordinator_node.services.feed_reader import FeedReader

try:
    from model_runner_client.grpc.generated.commons_pb2 import (
        Argument,
        Variant,
        VariantType,
    )
    from model_runner_client.model_concurrent_runners.dynamic_subclass_model_concurrent_runner import (
        DynamicSubclassModelConcurrentRunner,
    )
    from model_runner_client.model_concurrent_runners.model_concurrent_runner import (
        ModelConcurrentRunner,
    )
    from model_runner_client.security.credentials import SecureCredentials
    from model_runner_client.security.gateway_credentials import GatewayCredentials
    from model_runner_client.utils.datatype_transformer import encode_data

    MODEL_RUNNER_PROTO_AVAILABLE = True
except Exception:  # pragma: no cover
    ModelConcurrentRunner = None  # type: ignore[misc,assignment]
    MODEL_RUNNER_PROTO_AVAILABLE = False


class PredictService:
    """Base: get data → run models → store predictions → resolve actuals."""

    def __init__(
        self,
        feed_reader: FeedReader,
        contract: CrunchConfig | None = None,
        input_repository: DBInputRepository | None = None,
        model_repository: DBModelRepository | None = None,
        prediction_repository: DBPredictionRepository | None = None,
        runner: ModelConcurrentRunner | None = None,
        model_runner_node_host: str = "model-orchestrator",
        model_runner_node_port: int = 9091,
        model_runner_timeout_seconds: float = 60,
        crunch_id: str = "starter-challenge",
        base_classname: str = "tracker.TrackerBase",
        gateway_cert_dir: str | None = None,
        secure_cert_dir: str | None = None,
        **kwargs,
    ):
        self.feed_reader = feed_reader
        self.contract = contract or CrunchConfig()

        self.input_repository = input_repository
        self.model_repository = model_repository
        self.prediction_repository = prediction_repository
        self.crunch_id = crunch_id
        self.base_classname = base_classname

        self._runner = runner
        self._runner_host = model_runner_node_host
        self._runner_port = model_runner_node_port
        self._runner_timeout = model_runner_timeout_seconds
        self._runner_initialized = False
        self._runner_sync_task = None
        self._gateway_cert_dir = gateway_cert_dir
        self._secure_cert_dir = secure_cert_dir

        self._known_models: dict[str, Model] = {}
        self.logger = logging.getLogger(type(self).__name__)
        self.stop_event = asyncio.Event()

    # ── 1. get data ──

    def get_data(self, now: datetime) -> InputRecord:
        """Fetch input, validate through raw_input_type, save to DB."""
        raw = self.feed_reader.get_input(now)
        validated = self.contract.raw_input_type.model_validate(raw)
        data = validated.model_dump()

        record = InputRecord(
            id=f"INP_{now.strftime('%Y%m%d_%H%M%S.%f')[:-3]}",
            raw_data=data,
            received_at=now,
        )
        if self.input_repository is not None:
            self.input_repository.save(record)

        return record

    # ── 2. store predictions ──

    async def _call_models(self, scope: dict[str, Any]) -> dict:
        """Send call to model runner using the configured method name."""
        method = self.contract.call_method.method
        return await self._runner.call(method, self._encode_predict(scope))

    async def _tick_models(self, inference_input: dict[str, Any]) -> None:
        """Send latest data to all models."""
        responses = await self._runner.call("tick", self._encode_tick(inference_input))
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
        resolvable_at: datetime,
        exec_time_ms: float = 0.0,
        config_id: str | None = None,
        timing_data: dict[str, Any] | None = None,
    ) -> PredictionRecord:
        """Construct a PredictionRecord from model runner output."""
        suffix = "ABS" if status == PredictionStatus.ABSENT else "PRE"
        safe_key = "".join(
            ch if ch.isalnum() or ch in "-_" else "_" for ch in scope_key
        )
        pred_id = (
            f"{suffix}_{model_id}_{safe_key}_{now.strftime('%Y%m%d_%H%M%S.%f')[:-3]}"
        )

        return PredictionRecord(
            id=pred_id,
            input_id=input_id,
            model_id=model_id,
            prediction_config_id=config_id,
            scope_key=scope_key,
            scope={k: v for k, v in scope.items() if k != "scope_key"},
            status=status,
            exec_time_ms=exec_time_ms,
            inference_output=output,
            performed_at=now,
            resolvable_at=resolvable_at,
            _timing=timing_data or {},
        )

    def _save(self, predictions: list[PredictionRecord]) -> None:
        """Persist prediction records to the repository."""
        if predictions:
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

    def _build_credentials(self) -> dict:
        """Build credential kwargs for the model runner.

        Connection modes (mutually exclusive, first match wins):
          1. GATEWAY_CERT_DIR → gateway TLS (TLS-terminating proxies, e.g. Phala CVM)
          2. SECURE_CERT_DIR  → mTLS (direct secure connection)
          3. Neither          → insecure (local development only)
        """
        import os

        gateway_credentials = None
        secure_credentials = None

        gateway_cert_dir = self._gateway_cert_dir or os.getenv("GATEWAY_CERT_DIR")
        secure_cert_dir = self._secure_cert_dir or os.getenv("SECURE_CERT_DIR")

        if gateway_cert_dir:
            gateway_credentials = GatewayCredentials.from_pem(
                key_pem=Path(os.path.join(gateway_cert_dir, "key.pem")).read_bytes(),
            )
            self.logger.info("Using gateway TLS credentials from %s", gateway_cert_dir)
        elif secure_cert_dir:
            secure_credentials = SecureCredentials.from_directory(path=secure_cert_dir)
            self.logger.info("Using mTLS secure credentials from %s", secure_cert_dir)
        else:
            self.logger.info("Using insecure connection (no credentials configured)")

        return dict(
            secure_credentials=secure_credentials,
            gateway_credentials=gateway_credentials,
        )

    async def init_runner(self) -> None:
        if self._runner is None:
            if not MODEL_RUNNER_PROTO_AVAILABLE:
                raise RuntimeError("model-runner-client dependency is required")
            self._runner = DynamicSubclassModelConcurrentRunner(
                host=self._runner_host,
                port=self._runner_port,
                crunch_id=self.crunch_id,
                base_classname=self.base_classname,
                timeout=self._runner_timeout,
                max_consecutive_failures=100,
                max_consecutive_timeouts=100,
                **self._build_credentials(),
            )
        if not self._runner_initialized:
            await self._runner.init()
            self._runner_sync_task = asyncio.create_task(self._runner.sync())
            self._runner_initialized = True

    async def shutdown(self) -> None:
        self.stop_event.set()
        if self._runner_sync_task is not None:
            self._runner_sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner_sync_task

    # ── model management ──

    def register_model(self, model: Model) -> None:
        self._known_models[model.id] = model
        self.model_repository.save(model)

    def validate_output(self, output: dict[str, Any]) -> str | None:
        """Validate model output against InferenceOutput schema.

        Returns None if valid, or an error string if invalid.
        Catches both type mismatches AND outputs where no keys match
        the schema (model returning the wrong format entirely).
        """
        try:
            output_type = self.contract.output_type
            schema_fields = set(output_type.model_fields.keys())

            # Check that at least one output key matches a schema field
            matching_keys = set(output.keys()) & schema_fields
            if schema_fields and not matching_keys:
                msg = (
                    f"Model output keys {set(output.keys())} do not match any "
                    f"InferenceOutput fields {schema_fields}. The model is likely "
                    f"returning the wrong schema."
                )
                self.logger.error("INFERENCE_OUTPUT_VALIDATION_ERROR: %s", msg)
                return msg

            validated = self.contract.output_type.model_validate(output)
            output.update(validated.model_dump())
            return None
        except Exception as exc:
            self.logger.error("INFERENCE_OUTPUT_VALIDATION_ERROR: %s", exc)
            return str(exc)

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

    # ── proto encoding ──

    @staticmethod
    def _encode_tick(inference_input: dict[str, Any]) -> tuple:
        if MODEL_RUNNER_PROTO_AVAILABLE:
            return (
                [
                    Argument(
                        position=1,
                        data=Variant(
                            type=VariantType.JSON,
                            value=encode_data(VariantType.JSON, inference_input),
                        ),
                    )
                ],
                [],
            )
        return (inference_input,)

    # ── proto type mapping ──

    _VARIANT_TYPE_MAP: dict[str, Any] = {}

    @classmethod
    def _get_variant_type(cls, type_name: str) -> Any:
        """Resolve a CallMethodArg type string to a VariantType enum value."""
        if not cls._VARIANT_TYPE_MAP and MODEL_RUNNER_PROTO_AVAILABLE:
            cls._VARIANT_TYPE_MAP.update(
                {
                    "STRING": VariantType.STRING,
                    "INT": VariantType.INT,
                    "FLOAT": VariantType.DOUBLE,
                    "DOUBLE": VariantType.DOUBLE,
                    "JSON": VariantType.JSON,
                }
            )
        return cls._VARIANT_TYPE_MAP.get(
            type_name.upper(), cls._VARIANT_TYPE_MAP.get("STRING")
        )

    def _encode_predict(self, scope: dict[str, Any]) -> tuple:
        """Encode arguments according to ``contract.call_method.args``.

        Each arg reads its value from the scope dict (falling back to the
        contract's default scope), converts to the declared type, and is
        packed as a proto Argument when the model-runner client is available.
        """
        call_args = self.contract.call_method.args
        scope_defaults = self.contract.scope.model_dump()

        # Resolve raw values
        raw_values: list[tuple[str, Any]] = []
        for arg in call_args:
            value = scope.get(arg.name)
            if value is None:
                value = scope_defaults.get(arg.name, "")
            # Coerce to declared type
            utype = arg.type.upper()
            if utype == "INT":
                value = int(value)
            elif utype == "FLOAT":
                value = float(value)
            elif utype == "STRING":
                value = str(value)
            # JSON left as-is
            raw_values.append((utype, value))

        if MODEL_RUNNER_PROTO_AVAILABLE:
            arguments = []
            for position, (utype, value) in enumerate(raw_values, start=1):
                vtype = self._get_variant_type(utype)
                arguments.append(
                    Argument(
                        position=position,
                        data=Variant(type=vtype, value=encode_data(vtype, value)),
                    )
                )
            return (arguments, [])

        return tuple(v for _, v in raw_values)
