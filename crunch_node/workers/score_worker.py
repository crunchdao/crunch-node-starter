from __future__ import annotations

import asyncio
import logging

from crunch_node.config.extensions import ExtensionSettings
from crunch_node.config.runtime import RuntimeSettings
from crunch_node.config_loader import load_config
from crunch_node.db import (
    DBCheckpointRepository,
    DBInputRepository,
    DBLeaderboardRepository,
    DBMerkleCycleRepository,
    DBMerkleNodeRepository,
    DBModelRepository,
    DBPredictionRepository,
    DBScoreRepository,
    DBSnapshotRepository,
    create_session,
)
from crunch_node.extensions.callable_resolver import resolve_callable
from crunch_node.services.feed_reader import FeedReader
from crunch_node.services.score import ScoreService


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        force=True,
    )


def build_service() -> ScoreService:
    extension_settings = ExtensionSettings.from_env()
    runtime_settings = RuntimeSettings.from_env()
    config = load_config()

    # CrunchConfig.scoring_function takes precedence over env var
    if config.scoring_function is not None:
        scoring_function = config.scoring_function
    else:
        scoring_function = resolve_callable(
            extension_settings.scoring_function,
            required_params=("prediction", "ground_truth"),
        )

    session = create_session()

    trading_state_repo = None
    if getattr(config, "cost_model", None) is not None:
        from crunch_node.db.trading_state_repository import TradingStateRepository

        trading_state_repo = TradingStateRepository(session)

    return ScoreService(
        checkpoint_interval_seconds=runtime_settings.checkpoint_interval_seconds,
        score_interval_seconds=runtime_settings.score_interval_seconds,
        scoring_function=scoring_function,
        feed_reader=FeedReader.from_env(),
        input_repository=DBInputRepository(session),
        prediction_repository=DBPredictionRepository(session),
        score_repository=DBScoreRepository(session),
        snapshot_repository=DBSnapshotRepository(session),
        model_repository=DBModelRepository(session),
        leaderboard_repository=DBLeaderboardRepository(session),
        checkpoint_repository=DBCheckpointRepository(session),
        merkle_cycle_repository=DBMerkleCycleRepository(session),
        merkle_node_repository=DBMerkleNodeRepository(session),
        config=config,
        trading_state_repository=trading_state_repo,
    )


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("score worker bootstrap")

    service = build_service()

    # Validate scoring IO at startup — catches field-name mismatches
    # between InferenceOutput, GroundTruth, and the scoring function
    # before any real predictions are scored.
    service.validate_scoring_io()

    await service.run()


if __name__ == "__main__":
    asyncio.run(main())
