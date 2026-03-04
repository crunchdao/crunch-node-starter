from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from crunch_node.feeds.contracts import (
    FeedDataRecord,
    FeedFetchRequest,
    FeedSubscription,
    SubjectDescriptor,
)


class FeedSink(Protocol):
    async def on_record(self, record: FeedDataRecord) -> None: ...


class FeedHandle(Protocol):
    async def stop(self) -> None: ...


class DataFeed(Protocol):
    """Generic runtime data feed contract.

    - list_subjects: provider-native discovery and capabilities
    - listen: push mode
    - fetch: pull mode (backfill + truth-window queries)
    """

    async def list_subjects(self) -> Sequence[SubjectDescriptor]: ...

    async def listen(self, sub: FeedSubscription, sink: FeedSink) -> FeedHandle: ...

    async def fetch(self, req: FeedFetchRequest) -> Sequence[FeedDataRecord]: ...
