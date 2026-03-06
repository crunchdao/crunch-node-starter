"""Combined feed-predict worker.

Merges feed ingestion and prediction into a single process for low latency.
Feed records flow directly to predictions without pg_notify or DB roundtrip.
"""

from __future__ import annotations

import asyncio
import logging

from crunch_node.config.runtime import RuntimeSettings
from crunch_node.config_loader import load_config
from crunch_node.db import (
    DBFeedRecordRepository,
    DBInputRepository,
    DBModelRepository,
    DBPredictionRepository,
    create_session,
)
from crunch_node.services.feed_data import (
    FeedDataService,
    FeedDataSettings,
    RepositorySink,
)
from crunch_node.services.feed_reader import FeedReader
from crunch_node.services.feed_window import FeedWindow
from crunch_node.services.predict import PredictService
from crunch_node.services.predict_sink import PredictSink
from crunch_node.services.realtime_predict import RealtimePredictService

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        force=True,
    )


def _resolve_service_class(config) -> type[PredictService]:
    cls = getattr(config, "predict_service_class", None)

    if cls is None:
        return RealtimePredictService

    if not isinstance(cls, type) or not issubclass(cls, PredictService):
        raise TypeError(
            f"predict_service_class must be a PredictService subclass, got {cls!r}"
        )

    return cls


def build_service() -> PredictService:
    """Build predict service with default dependencies.

    Backward-compatible entry point for tests and standalone usage.
    """
    runtime_settings = RuntimeSettings.from_env()
    config = load_config()
    session = create_session()

    from crunch_node.metrics.timing import timing_collector

    timing_collector.configure(
        enabled=config.performance.timing_enabled,
        buffer_size=config.performance.timing_buffer_size,
    )

    return build_predict_service(session, config, runtime_settings)


def build_predict_service(session, config, runtime_settings) -> PredictService:
    service_class = _resolve_service_class(config)
    logger.info("Using predict service: %s", service_class.__name__)

    kwargs = dict(
        feed_reader=FeedReader.from_env(),
        config=config,
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

    if issubclass(service_class, RealtimePredictService):
        kwargs["checkpoint_interval_seconds"] = (
            runtime_settings.checkpoint_interval_seconds
        )
        if config.post_predict_hook is not None:
            kwargs["post_predict_hook"] = config.post_predict_hook

    return service_class(**kwargs)


async def main() -> None:
    configure_logging()
    logger.info("combined feed-predict worker bootstrap")

    runtime_settings = RuntimeSettings.from_env()
    config = load_config()
    session = create_session()

    from crunch_node.metrics.timing import timing_collector

    timing_collector.configure(
        enabled=config.performance.timing_enabled,
        buffer_size=config.performance.timing_buffer_size,
    )

    predict_service = build_predict_service(session, config, runtime_settings)
    # init_runner() is called lazily by run_once() when first prediction happens

    feed_settings = FeedDataSettings.from_env()
    feed_repository = DBFeedRecordRepository(session)

    feed_window = FeedWindow(max_size=120)
    logger.info("Loading initial feed window from database")
    feed_window.load_from_db(feed_repository, feed_settings)

    predict_sink = PredictSink(
        predict_service=predict_service,
        feed_window=feed_window,
    )
    repo_sink = RepositorySink(feed_repository)

    feed_service = FeedDataService(
        settings=feed_settings,
        feed_record_repository=feed_repository,
        sinks=[repo_sink, predict_sink],
    )

    logger.info(
        "Starting combined worker: source=%s subjects=%s",
        feed_settings.source,
        ",".join(feed_settings.subjects),
    )

    try:
        await feed_service.run()
    finally:
        await predict_service.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
