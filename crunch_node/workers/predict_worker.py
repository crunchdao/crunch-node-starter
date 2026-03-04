from __future__ import annotations

import asyncio
import logging

from crunch_node.config.runtime import RuntimeSettings
from crunch_node.config_loader import load_config
from crunch_node.db import (
    DBInputRepository,
    DBModelRepository,
    DBPredictionRepository,
    create_session,
)
from crunch_node.services.feed_reader import FeedReader
from crunch_node.services.predict import PredictService
from crunch_node.services.realtime_predict import RealtimePredictService

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        force=True,
    )


def _resolve_service_class(config) -> type[PredictService]:
    """Resolve the predict service class from CrunchConfig.

    Priority:
      1. ``config.predict_service_class`` (explicit override)
      2. ``RealtimePredictService`` (default)

    Validates that the resolved class is a PredictService subclass.
    """
    cls = getattr(config, "predict_service_class", None)

    if cls is None:
        return RealtimePredictService

    if not isinstance(cls, type) or not issubclass(cls, PredictService):
        raise TypeError(
            f"predict_service_class must be a PredictService subclass, got {cls!r}"
        )

    return cls


def build_service() -> PredictService:
    runtime_settings = RuntimeSettings.from_env()
    config = load_config()
    session = create_session()

    service_class = _resolve_service_class(config)
    logger.info("Using predict service: %s", service_class.__name__)

    kwargs = dict(
        feed_reader=FeedReader.from_env(),
        contract=config,
        input_repository=DBInputRepository(session),
        model_repository=DBModelRepository(session),
        prediction_repository=DBPredictionRepository(session),
        model_runner_node_host=runtime_settings.model_runner_node_host,
        model_runner_node_port=runtime_settings.model_runner_node_port,
        model_runner_timeout_seconds=runtime_settings.model_runner_timeout_seconds,
        crunch_id=runtime_settings.crunch_id,
        base_classname=runtime_settings.base_classname,
        gateway_cert_dir=runtime_settings.gateway_cert_dir,
        secure_cert_dir=runtime_settings.secure_cert_dir,
    )

    # Pass checkpoint_interval_seconds only if the class accepts it
    # (RealtimePredictService does, base PredictService does not)
    if issubclass(service_class, RealtimePredictService):
        kwargs["checkpoint_interval_seconds"] = (
            runtime_settings.checkpoint_interval_seconds
        )
        if config.post_predict_hook is not None:
            kwargs["post_predict_hook"] = config.post_predict_hook

    return service_class(**kwargs)


async def main() -> None:
    configure_logging()
    logger.info("predict worker bootstrap")

    service = build_service()
    await service.run()


if __name__ == "__main__":
    asyncio.run(main())
