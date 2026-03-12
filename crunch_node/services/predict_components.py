"""Shared predict-service components extracted from PredictService.

These components are intentionally small and mode-agnostic so realtime and
tournament services can share cross-mode behavior without sharing orchestration.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from crunch_node.entities.model import Model
from crunch_node.entities.prediction import PredictionRecord, PredictionStatus

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
    from model_runner_client.utils.datatype_transformer import encode_data

    MODEL_RUNNER_PROTO_AVAILABLE = True
except Exception:  # pragma: no cover
    ModelConcurrentRunner = Any  # type: ignore[assignment]
    MODEL_RUNNER_PROTO_AVAILABLE = False

try:
    from model_runner_client.security.gateway_credentials import GatewayCredentials
except ImportError:
    GatewayCredentials = None  # type: ignore[assignment,misc]


class ModelRegistry:
    """Tracks known models and syncs model metadata to the repository.

    Model metadata persistence is treated as non-critical for predict-loop
    continuity: failures are logged and do not interrupt model execution flow.
    """

    def __init__(
        self,
        known_models: dict[str, Model] | None = None,
        model_repository: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.known_models = known_models if known_models is not None else {}
        self.model_repository = model_repository
        self.logger = logger or logging.getLogger(__name__)
        self._dirty_model_ids: set[str] = set()

    @staticmethod
    def _fingerprint(model: Model) -> tuple[str, str, str, str, str]:
        """Semantic identity of model metadata (ignores timestamps/scores)."""
        return (
            model.id,
            model.name,
            model.player_id,
            model.player_name,
            model.deployment_identifier,
        )

    def register(self, model: Model) -> None:
        existing = self.known_models.get(model.id)
        if existing is not None and self._fingerprint(existing) == self._fingerprint(
            model
        ):
            return

        self.known_models[model.id] = model
        self._dirty_model_ids.add(model.id)

    def flush_non_critical(self) -> None:
        """Best-effort persistence for dirty model metadata.

        Called before prediction persistence to ensure foreign key integrity.
        Models must exist in database before predictions that reference them.
        """
        if self.model_repository is None or not self._dirty_model_ids:
            return

        for model_id in list(self._dirty_model_ids):
            model = self.known_models.get(model_id)
            if model is None:
                self._dirty_model_ids.discard(model_id)
                continue

            try:
                self.model_repository.save(model)
                self._dirty_model_ids.discard(model_id)
            except Exception as exc:
                self.logger.warning(
                    "non-critical model persistence failed for model_id=%s: %s",
                    model.id,
                    exc,
                )


class OutputValidator:
    """Validates and normalizes model outputs against contract.output_type."""

    def __init__(self, output_type: type, logger: logging.Logger | None = None) -> None:
        self.output_type = output_type
        self.logger = logger or logging.getLogger(__name__)

    def validate_and_normalize(self, output: dict[str, Any]) -> str | None:
        """Validate output in-place.

        Returns:
            None when valid, otherwise an error string.
        """
        try:
            schema_fields = set(self.output_type.model_fields.keys())

            # Check that at least one output key matches a schema field.
            matching_keys = set(output.keys()) & schema_fields
            if schema_fields and not matching_keys:
                msg = (
                    f"Model output keys {set(output.keys())} do not match any "
                    f"InferenceOutput fields {schema_fields}. The model is likely "
                    f"returning the wrong schema."
                )
                self.logger.error("INFERENCE_OUTPUT_VALIDATION_ERROR: %s", msg)
                return msg

            validated = self.output_type.model_validate(output)
            output.update(validated.model_dump())
            return None
        except Exception as exc:
            self.logger.error("INFERENCE_OUTPUT_VALIDATION_ERROR: %s", exc)
            return str(exc)


class PredictionRecordFactory:
    """Builds PredictionRecord instances with stable ID/status conventions."""

    @staticmethod
    def build(
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
        suffix = "ABS" if status == PredictionStatus.ABSENT else "PRE"
        safe_key = "".join(
            ch if ch.isalnum() or ch in "-_" else "_" for ch in scope_key
        )
        pred_id = (
            f"{suffix}_{model_id}_{safe_key}_{now.strftime('%Y%m%d_%H%M%S.%f')[:-3]}"
        )

        meta = {}
        if timing_data:
            meta["timing"] = timing_data

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
            meta=meta,
            performed_at=now,
            resolvable_at=resolvable_at,
        )


class PredictionKernel:
    """Shared runner lifecycle + transport/encoding primitives."""

    _VARIANT_TYPE_MAP: dict[str, Any] = {}

    def __init__(
        self,
        *,
        runner: ModelConcurrentRunner | None = None,
        model_runner_node_host: str = "model-orchestrator",
        model_runner_node_port: int = 9091,
        model_runner_timeout_seconds: float = 60,
        crunch_id: str = "starter-challenge",
        base_classname: str = "cruncher.BaseModelClass",
        gateway_cert_dir: str | None = None,
        secure_cert_dir: str | None = None,
        logger: logging.Logger | None = None,
        proto_available: bool | None = None,
    ) -> None:
        self.runner = runner
        self._runner_host = model_runner_node_host
        self._runner_port = model_runner_node_port
        self._runner_timeout = model_runner_timeout_seconds
        self._crunch_id = crunch_id
        self._base_classname = base_classname
        self._gateway_cert_dir = gateway_cert_dir
        self._secure_cert_dir = secure_cert_dir
        self._runner_initialized = False
        self.runner_sync_task: asyncio.Task | None = None
        self.logger = logger or logging.getLogger(__name__)
        self._proto_available = (
            MODEL_RUNNER_PROTO_AVAILABLE
            if proto_available is None
            else bool(proto_available)
        )

    def _build_credentials(self) -> dict[str, Any]:
        gateway_credentials = None
        secure_credentials = None

        gateway_cert_dir = self._gateway_cert_dir or os.getenv("GATEWAY_CERT_DIR")
        secure_cert_dir = self._secure_cert_dir or os.getenv("SECURE_CERT_DIR")

        if (
            gateway_cert_dir
            and self._proto_available
            and GatewayCredentials is not None
        ):
            gateway_credentials = GatewayCredentials.from_pem(
                key_pem=Path(os.path.join(gateway_cert_dir, "key.pem")).read_bytes(),
            )
            self.logger.info("Using gateway TLS credentials from %s", gateway_cert_dir)
        elif secure_cert_dir and self._proto_available:
            secure_credentials = SecureCredentials.from_directory(path=secure_cert_dir)
            self.logger.info("Using mTLS secure credentials from %s", secure_cert_dir)
        else:
            self.logger.info("Using insecure connection (no credentials configured)")

        result = {}
        if secure_credentials is not None:
            result["secure_credentials"] = secure_credentials
        if gateway_credentials is not None:
            result["gateway_credentials"] = gateway_credentials
        return result

    async def init_runner(self) -> None:
        if self.runner is None:
            if not self._proto_available:
                raise RuntimeError("model-runner-client dependency is required")
            self.runner = DynamicSubclassModelConcurrentRunner(
                host=self._runner_host,
                port=self._runner_port,
                crunch_id=self._crunch_id,
                base_classname=self._base_classname,
                timeout=self._runner_timeout,
                max_consecutive_failures=100,
                max_consecutive_timeouts=100,
                **self._build_credentials(),
            )

        if not self._runner_initialized:
            await self.runner.init()
            self.runner_sync_task = asyncio.create_task(self.runner.sync())
            self._runner_initialized = True

    async def shutdown(self) -> None:
        if self.runner_sync_task is not None:
            self.runner_sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.runner_sync_task
            self.runner_sync_task = None

    async def replace_runner(self, runner: ModelConcurrentRunner | None) -> None:
        """Swap the active runner instance (used by tests and hot-reload flows)."""
        if runner is self.runner:
            return

        await self.shutdown()
        self.runner = runner
        self._runner_initialized = False

    async def call(self, method: str, args: tuple) -> dict:
        if self.runner is None:
            raise RuntimeError("Runner is not initialized")
        return await self.runner.call(method, args)

    @classmethod
    def get_variant_type(cls, type_name: str) -> Any:
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

    def encode_feed_update(self, inference_input: dict[str, Any]) -> tuple:
        if self._proto_available:
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

    def encode_predict(
        self,
        *,
        scope: dict[str, Any],
        call_args: list[Any],
        scope_defaults: dict[str, Any],
    ) -> tuple:
        raw_values: list[tuple[str, Any]] = []
        for arg in call_args:
            value = scope.get(arg.name)
            if value is None:
                value = scope_defaults.get(arg.name, "")

            utype = arg.type.upper()
            if utype == "INT":
                value = int(value)
            elif utype == "FLOAT":
                value = float(value)
            elif utype == "STRING":
                value = str(value)

            raw_values.append((utype, value))

        if self._proto_available:
            arguments = []
            for position, (utype, value) in enumerate(raw_values, start=1):
                vtype = self.get_variant_type(utype)
                arguments.append(
                    Argument(
                        position=position,
                        data=Variant(type=vtype, value=encode_data(vtype, value)),
                    )
                )
            return (arguments, [])

        return tuple(v for _, v in raw_values)
