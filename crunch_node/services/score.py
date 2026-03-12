"""Score service: thin orchestrator for the scoring pipeline."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from crunch_node.services.scoring_strategy import EnsembleStrategy, ScoringStrategy


class ScoreService:
    def __init__(
        self,
        scoring_strategy: ScoringStrategy,
        ensemble_strategy: EnsembleStrategy | None = None,
        leaderboard_service=None,
        merkle_service=None,
        checkpoint_service=None,
        score_interval_seconds: int = 60,
    ):
        self.scoring_strategy = scoring_strategy
        self.ensemble_strategy = ensemble_strategy
        self.leaderboard_service = leaderboard_service
        self.merkle_service = merkle_service
        self.checkpoint_service = checkpoint_service
        self.score_interval_seconds = score_interval_seconds
        self.logger = logging.getLogger(__name__)
        self.stop_event = asyncio.Event()

    def score_and_snapshot(self) -> bool:
        now = datetime.now(UTC)

        snapshots = self.scoring_strategy.produce_snapshots(now)
        if not snapshots:
            self.logger.info("No snapshots produced this cycle")
            return False

        if self.ensemble_strategy:
            ensemble_snapshots = self.ensemble_strategy.compute_ensembles(snapshots, now)
            snapshots += ensemble_snapshots

        if self.merkle_service and snapshots:
            try:
                self.merkle_service.commit_cycle(snapshots, now)
            except Exception as exc:
                self.logger.warning("Merkle cycle commit failed: %s", exc)

        if self.leaderboard_service:
            self.leaderboard_service.rebuild()

        if self.checkpoint_service:
            self.checkpoint_service.maybe_checkpoint(now)

        return True

    async def run(self) -> None:
        self.logger.info(
            "score service started (score_interval=%ds)",
            self.score_interval_seconds,
        )
        while not self.stop_event.is_set():
            try:
                self.score_and_snapshot()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.exception("score loop error: %s", exc)
                self._rollback()
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.score_interval_seconds
                )
            except TimeoutError:
                pass

    async def shutdown(self) -> None:
        self.stop_event.set()

    def _rollback(self) -> None:
        rollback = getattr(self.scoring_strategy, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception as exc:
                self.logger.warning("Strategy rollback failed: %s", exc)
