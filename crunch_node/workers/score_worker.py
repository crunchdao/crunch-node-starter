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
from crunch_node.services.checkpoint import CheckpointService, EmissionConfig
from crunch_node.services.feed_reader import FeedReader
from crunch_node.services.leaderboard import LeaderboardService
from crunch_node.services.prediction_scorer import PredictionScorer
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

    session = create_session()
    snapshot_repo = DBSnapshotRepository(session)
    model_repo = DBModelRepository(session)

    if config.build_score_snapshots is not None:
        scoring_strategy = config.build_score_snapshots(
            session=session, config=config, snapshot_repository=snapshot_repo
        )
    else:
        if config.scoring_function is not None:
            scoring_function = config.scoring_function
        else:
            scoring_function = resolve_callable(
                extension_settings.scoring_function,
                required_params=("prediction", "ground_truth"),
            )

        scoring_strategy = PredictionScorer(
            scoring_function=scoring_function,
            feed_reader=FeedReader.from_env(),
            input_repository=DBInputRepository(session),
            prediction_repository=DBPredictionRepository(session),
            score_repository=DBScoreRepository(session),
            snapshot_repository=snapshot_repo,
            config=config,
        )

    ensemble_strategy = None
    if config.ensembles and isinstance(scoring_strategy, PredictionScorer):
        from crunch_node.services.prediction_ensemble import (
            PredictionEnsembleStrategy,
        )

        ensemble_strategy = PredictionEnsembleStrategy(
            config=config,
            scorer=scoring_strategy,
            prediction_repository=scoring_strategy.prediction_repository,
            score_repository=scoring_strategy.score_repository,
            snapshot_repository=scoring_strategy.snapshot_repository,
        )

    leaderboard_service = LeaderboardService(
        snapshot_repository=snapshot_repo,
        model_repository=model_repo,
        leaderboard_repository=DBLeaderboardRepository(session),
        aggregation=config.aggregation,
    )

    merkle_service = MerkleService(
        merkle_cycle_repository=DBMerkleCycleRepository(session),
        merkle_node_repository=DBMerkleNodeRepository(session),
    )

    checkpoint_service = CheckpointService(
        snapshot_repository=snapshot_repo,
        checkpoint_repository=DBCheckpointRepository(session),
        model_repository=model_repo,
        emission=EmissionConfig(
            build_emission=config.build_emission,
            crunch_pubkey=config.crunch_pubkey,
            compute_provider=config.compute_provider,
            data_provider=config.data_provider,
        ),
        interval_seconds=runtime_settings.checkpoint_interval_seconds,
        merkle_service=merkle_service,
        ranking_key=config.aggregation.ranking_key,
        ranking_direction=config.aggregation.ranking_direction,
    )

    return ScoreService(
        scoring_strategy=scoring_strategy,
        ensemble_strategy=ensemble_strategy,
        leaderboard_service=leaderboard_service,
        merkle_service=merkle_service,
        checkpoint_service=checkpoint_service,
        score_interval_seconds=runtime_settings.score_interval_seconds,
    )


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("score worker bootstrap")

    service = build_service()

    if isinstance(service.scoring_strategy, PredictionScorer):
        service.scoring_strategy.validate_scoring_io()

    await service.run()


if __name__ == "__main__":
    asyncio.run(main())
