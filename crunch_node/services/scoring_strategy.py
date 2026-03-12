"""Protocols for pluggable scoring and ensemble strategies."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from crunch_node.entities.prediction import SnapshotRecord


class ScoringStrategy(Protocol):
    def produce_snapshots(self, now: datetime) -> list[SnapshotRecord]: ...
    def rollback(self) -> None: ...


class EnsembleStrategy(Protocol):
    def compute_ensembles(
        self, snapshots: list[SnapshotRecord], now: datetime
    ) -> list[SnapshotRecord]: ...
