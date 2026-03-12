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
from crunch_node.merkle.service import MerkleService
from crunch_node.services.checkpoint import CheckpointService
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

    snapshot_repo = DBSnapshotRepository(session)
    model_repo = DBModelRepository(session)

    merkle_service = MerkleService(
        merkle_cycle_repository=DBMerkleCycleRepository(session),
        merkle_node_repository=DBMerkleNodeRepository(session),
    )

    checkpoint_service = CheckpointService(
        snapshot_repository=snapshot_repo,
        checkpoint_repository=DBCheckpointRepository(session),
        model_repository=model_repo,
        config=config,
        interval_seconds=runtime_settings.checkpoint_interval_seconds,
        merkle_service=merkle_service,
        build_emission=config.build_emission,
    )

    build_snapshots_fn = None
    if config.build_score_snapshots is not None:
        build_snapshots_fn = config.build_score_snapshots(
            session=session, config=config, snapshot_repository=snapshot_repo
        )

    return ScoreService(
        checkpoint_interval_seconds=runtime_settings.checkpoint_interval_seconds,
        score_interval_seconds=runtime_settings.score_interval_seconds,
        scoring_function=scoring_function,
        feed_reader=FeedReader.from_env(),
        input_repository=DBInputRepository(session),
        prediction_repository=DBPredictionRepository(session),
        score_repository=DBScoreRepository(session),
        snapshot_repository=snapshot_repo,
        model_repository=model_repo,
        leaderboard_repository=DBLeaderboardRepository(session),
        checkpoint_service=checkpoint_service,
        merkle_service=merkle_service,
        config=config,
        build_snapshots_fn=build_snapshots_fn,
    )


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("score worker bootstrap")

    service = build_service()

    if service._build_snapshots_fn is None:
        service.validate_scoring_io()

    await service.run()


if __name__ == "__main__":
    asyncio.run(main())
