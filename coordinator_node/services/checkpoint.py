"""Checkpoint service: aggregate snapshots into on-chain emission checkpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from coordinator_node.crunch_config import CrunchConfig
from coordinator_node.db.repositories import (
    DBCheckpointRepository,
    DBModelRepository,
    DBSnapshotRepository,
)
from coordinator_node.entities.prediction import CheckpointRecord, CheckpointStatus
from coordinator_node.merkle.service import MerkleService


class CheckpointService:
    """Creates emission checkpoints from accumulated snapshots.

    Can be used standalone or composed inside ScoreService for single-container
    deployments.
    """

    def __init__(
        self,
        snapshot_repository: DBSnapshotRepository,
        checkpoint_repository: DBCheckpointRepository,
        model_repository: DBModelRepository,
        contract: CrunchConfig | None = None,
        interval_seconds: int = 7 * 24 * 3600,  # weekly
        merkle_service: MerkleService | None = None,
    ):
        self.snapshot_repository = snapshot_repository
        self.checkpoint_repository = checkpoint_repository
        self.model_repository = model_repository
        self.contract = contract or CrunchConfig()
        self.interval_seconds = interval_seconds
        self.merkle_service = merkle_service
        self.logger = logging.getLogger(__name__)

    def create_checkpoint(self) -> CheckpointRecord | None:
        now = datetime.now(UTC)

        # Determine period start: end of last checkpoint, or beginning of time
        last = self.checkpoint_repository.get_latest()
        period_start = (
            last.period_end if last else now - timedelta(seconds=self.interval_seconds)
        )

        # Get all snapshots in this period
        snapshots = self.snapshot_repository.find(since=period_start, until=now)
        if not snapshots:
            self.logger.info(
                "No snapshots since %s, skipping checkpoint", period_start.isoformat()
            )
            return None

        models = self.model_repository.fetch_all()
        aggregation = self.contract.aggregation

        # Aggregate snapshots per model
        by_model: dict[str, list] = {}
        for snap in snapshots:
            by_model.setdefault(snap.model_id, []).append(snap)

        ranked_entries: list[dict[str, Any]] = []
        for model_id, model_snapshots in by_model.items():
            # Weighted average by prediction count
            total_preds = sum(s.prediction_count for s in model_snapshots)
            if total_preds == 0:
                continue

            summary: dict[str, float] = {}
            for snap in model_snapshots:
                weight = snap.prediction_count / total_preds
                for key, value in snap.result_summary.items():
                    if isinstance(value, (int, float)):
                        summary[key] = summary.get(key, 0.0) + float(value) * weight

            model = models.get(model_id)
            ranked_entries.append(
                {
                    "model_id": model_id,
                    "model_name": model.name if model else None,
                    "cruncher_name": model.player_name if model else None,
                    "prediction_count": total_preds,
                    "snapshot_count": len(model_snapshots),
                    "result_summary": summary,
                }
            )

        # Rank by the aggregation ranking key
        ranking_key = aggregation.ranking_key
        reverse = aggregation.ranking_direction == "desc"
        ranked_entries.sort(
            key=lambda e: float(e.get("result_summary", {}).get(ranking_key, 0)),
            reverse=reverse,
        )
        for idx, entry in enumerate(ranked_entries, start=1):
            entry["rank"] = idx

        # Build emission checkpoint → protocol format for on-chain submission
        emission = self.contract.build_emission(
            ranked_entries,
            crunch_pubkey=self.contract.crunch_pubkey,
            compute_provider=self.contract.compute_provider,
            data_provider=self.contract.data_provider,
        )

        checkpoint = CheckpointRecord(
            id=f"CKP_{now.strftime('%Y%m%d_%H%M%S')}",
            period_start=period_start,
            period_end=now,
            status=CheckpointStatus.PENDING,
            entries=[emission],
            meta={
                "snapshot_count": len(snapshots),
                "model_count": len(ranked_entries),
                "ranking": ranked_entries,
            },
            created_at=now,
        )

        self.checkpoint_repository.save(checkpoint)

        # Merkle tamper evidence: build tree over cycle roots
        if self.merkle_service:
            try:
                merkle_root = self.merkle_service.commit_checkpoint(
                    checkpoint_id=checkpoint.id,
                    period_start=period_start,
                    period_end=now,
                    now=now,
                )
                if merkle_root:
                    self.checkpoint_repository.update_merkle_root(
                        checkpoint.id, merkle_root
                    )
                    self.logger.info(
                        "Checkpoint %s merkle_root=%s", checkpoint.id, merkle_root[:16]
                    )
            except Exception as exc:
                self.logger.warning("Merkle checkpoint commit failed: %s", exc)

        self.logger.info(
            "Created checkpoint %s: %d models, %d snapshots, period %s → %s",
            checkpoint.id,
            len(ranked_entries),
            len(snapshots),
            period_start.isoformat(),
            now.isoformat(),
        )
        return checkpoint
