"""Repository Protocol definitions for polymorphic usage."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from crunch_node.entities.feed_record import FeedIngestionState, FeedRecord


@runtime_checkable
class FeedRecordRepository(Protocol):
    """Minimal feed-record storage interface used by backfill and ingestion."""

    def append_records(self, records: Iterable[FeedRecord]) -> int: ...

    def set_watermark(self, state: FeedIngestionState) -> None: ...
