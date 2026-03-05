"""Checkpoint worker: periodically aggregates snapshots into checkpoints.

DEPRECATED: The checkpoint-worker container is no longer needed.
Checkpointing is now handled by the score worker. This module is kept
for backward compatibility — it re-exports CheckpointService and can
still be run standalone if desired.
"""

from __future__ import annotations

import asyncio
import logging
import os
import warnings

from crunch_node.config_loader import load_config
from crunch_node.db import (
    DBCheckpointRepository,
    DBMerkleCycleRepository,
    DBMerkleNodeRepository,
    DBModelRepository,
    DBSnapshotRepository,
    create_session,
)
from crunch_node.merkle.service import MerkleService

# Re-export for backward compatibility (tests import from here)
from crunch_node.services.checkpoint import CheckpointService  # noqa: F401


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        force=True,
    )


def build_service() -> CheckpointService:
    session = create_session()
    interval = int(os.getenv("CHECKPOINT_INTERVAL_SECONDS", str(7 * 24 * 3600)))

    config = load_config()

    # Env var overrides for on-chain identifiers (backward compat)
    crunch_pubkey = os.getenv("CRUNCH_PUBKEY", "")
    compute_provider = os.getenv("COMPUTE_PROVIDER_PUBKEY")
    data_provider = os.getenv("DATA_PROVIDER_PUBKEY")

    if crunch_pubkey:
        config.crunch_pubkey = crunch_pubkey
    if compute_provider:
        config.compute_provider = compute_provider
    if data_provider:
        config.data_provider = data_provider

    merkle_service = MerkleService(
        merkle_cycle_repository=DBMerkleCycleRepository(session),
        merkle_node_repository=DBMerkleNodeRepository(session),
    )

    return CheckpointService(
        snapshot_repository=DBSnapshotRepository(session),
        checkpoint_repository=DBCheckpointRepository(session),
        model_repository=DBModelRepository(session),
        config=config,
        interval_seconds=interval,
        merkle_service=merkle_service,
    )


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    warnings.warn(
        "checkpoint_worker is deprecated — checkpointing is now handled by "
        "the score worker. Remove the checkpoint-worker container.",
        DeprecationWarning,
        stacklevel=1,
    )
    logger.info(
        "coordinator checkpoint worker bootstrap (deprecated — use score worker)"
    )

    service = build_service()

    # Run standalone loop for backward compat
    stop_event = asyncio.Event()
    logger.info("checkpoint worker started (interval=%ds)", service.interval_seconds)
    while not stop_event.is_set():
        try:
            service.create_checkpoint()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("checkpoint error: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=service.interval_seconds)
        except TimeoutError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
