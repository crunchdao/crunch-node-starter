"""Merkle tree tables: cycle hashes and tree nodes for tamper evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

TZDateTime = DateTime(timezone=True)


def utc_now() -> datetime:
    return datetime.now(UTC)


class MerkleCycleRow(SQLModel, table=True):
    """One row per score cycle. Chains to previous cycle for tamper detection."""

    __tablename__ = "merkle_cycles"

    id: str = Field(primary_key=True)
    previous_cycle_id: str | None = Field(default=None, index=True)
    previous_cycle_root: str | None = Field(default=None)
    snapshots_root: str
    chained_root: str = Field(index=True)
    snapshot_count: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=utc_now, index=True, sa_type=TZDateTime
    )


class MerkleNodeRow(SQLModel, table=True):
    """Nodes in a Merkle tree — leaves, intermediates, and roots."""

    __tablename__ = "merkle_nodes"

    id: str = Field(primary_key=True)
    checkpoint_id: str | None = Field(
        default=None,
        foreign_key="checkpoints.id",
        index=True,
    )
    cycle_id: str | None = Field(
        default=None,
        foreign_key="merkle_cycles.id",
        index=True,
    )
    level: int = Field(default=0)
    position: int = Field(default=0)
    hash: str
    left_child_id: str | None = Field(default=None)
    right_child_id: str | None = Field(default=None)
    snapshot_id: str | None = Field(
        default=None,
        foreign_key="snapshots.id",
        index=True,
    )
    snapshot_content_hash: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now, sa_type=TZDateTime)
