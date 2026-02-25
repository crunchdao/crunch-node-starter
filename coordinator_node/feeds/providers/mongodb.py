"""Generic MongoDB feed provider.

Connects to any MongoDB collection and exposes it as a feed source.
All field mapping is configurable via FEED_OPT_* environment variables:

    FEED_SOURCE=mongodb
    FEED_OPT_mongodb_uri=mongodb://user:pass@host:27017/?...
    FEED_OPT_database=my_database
    FEED_OPT_collection=my_collection
    FEED_OPT_timestamp_field=blockTime       # field for time ordering (unix seconds)
    FEED_OPT_subject_field=mint              # field used as subject identifier
    FEED_OPT_poll_seconds=5                  # polling interval
    FEED_OPT_inserted_at_field=insertedAt    # field for tailing new documents

Supports two listen modes:
- Change streams (preferred, requires replica set)
- Polling by inserted_at_field (fallback)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from coordinator_node.feeds.base import DataFeed, FeedHandle, FeedSink
from coordinator_node.feeds.contracts import (
    FeedDataRecord,
    FeedFetchRequest,
    FeedSubscription,
    SubjectDescriptor,
)
from coordinator_node.feeds.registry import FeedSettings

try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Default options
_DEFAULTS = {
    "mongodb_uri": "mongodb://localhost:27017",
    "database": "test",
    "collection": "events",
    "timestamp_field": "blockTime",
    "subject_field": "symbol",
    "inserted_at_field": "insertedAt",
    "poll_seconds": "5",
    "subject_limit": "500",
}


def _opt(settings: FeedSettings, key: str) -> str:
    """Read an option from settings with fallback to defaults."""
    return settings.options.get(key, _DEFAULTS.get(key, ""))


class _MongoConnection:
    """Lazy MongoDB connection wrapper."""

    def __init__(self, settings: FeedSettings):
        self._settings = settings
        self._client: Any | None = None
        self._collection: Any | None = None

    def _connect(self) -> Any:
        if MongoClient is None:
            raise ImportError(
                "pymongo is required for the mongodb feed provider. "
                "Install it with: pip install pymongo"
            )

        if self._client is None:
            uri = _opt(self._settings, "mongodb_uri")
            self._client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
            db = self._client[_opt(self._settings, "database")]
            self._collection = db[_opt(self._settings, "collection")]
            logger.info(
                "mongodb feed connected to %s.%s",
                _opt(self._settings, "database"),
                _opt(self._settings, "collection"),
            )

        return self._collection

    @property
    def collection(self) -> Any:
        return self._connect()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._collection = None


class _PollingFeedHandle:
    def __init__(self, task: asyncio.Task[None]):
        self._task = task

    async def stop(self) -> None:
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


def _doc_to_record(
    doc: dict[str, Any],
    *,
    subject_field: str,
    timestamp_field: str,
    kind: str,
    granularity: str,
) -> FeedDataRecord | None:
    """Convert a MongoDB document to a FeedDataRecord.

    The entire document (minus internal fields) is placed in ``values``.
    The workspace's CrunchConfig.raw_input_type defines how to interpret it.
    """
    subject = doc.get(subject_field)
    if subject is None:
        return None

    ts_raw = doc.get(timestamp_field)
    if ts_raw is None:
        return None

    # Handle both unix timestamps (int) and datetime objects
    if isinstance(ts_raw, datetime):
        ts_event = int(ts_raw.timestamp())
    elif isinstance(ts_raw, (int, float)):
        ts_event = int(ts_raw)
    else:
        return None

    # Build values dict — include all fields except internal MongoDB fields
    values: dict[str, Any] = {}
    for key, value in doc.items():
        if key.startswith("_"):
            continue
        # Convert datetime objects to ISO strings for JSON compatibility
        if isinstance(value, datetime):
            values[key] = value.isoformat()
        else:
            values[key] = value

    return FeedDataRecord(
        source="mongodb",
        subject=str(subject),
        kind=kind,
        granularity=granularity,
        ts_event=ts_event,
        values=values,
        metadata={},
    )


class MongoDBFeed(DataFeed):
    """Generic MongoDB feed provider.

    Reads documents from a MongoDB collection and serves them as feed records.
    Field mapping is fully configurable via FeedSettings options.
    """

    def __init__(self, settings: FeedSettings):
        self.settings = settings
        self._conn = _MongoConnection(settings)
        self._subject_field = _opt(settings, "subject_field")
        self._timestamp_field = _opt(settings, "timestamp_field")
        self._inserted_at_field = _opt(settings, "inserted_at_field")
        self._poll_seconds = float(_opt(settings, "poll_seconds"))
        self._subject_limit = int(_opt(settings, "subject_limit"))

    async def list_subjects(self) -> Sequence[SubjectDescriptor]:
        """Discover subjects by querying distinct values of the subject field."""
        try:
            coll = self._conn.collection
            subjects = await asyncio.to_thread(coll.distinct, self._subject_field)
        except Exception as exc:
            logger.warning("mongodb list_subjects failed: %s", exc)
            return []

        descriptors: list[SubjectDescriptor] = []
        for subject in subjects[: self._subject_limit]:
            descriptors.append(
                SubjectDescriptor(
                    symbol=str(subject),
                    display_name=str(subject),
                    kinds=("event",),
                    granularities=("event",),
                    source="mongodb",
                )
            )

        return descriptors

    async def listen(self, sub: FeedSubscription, sink: FeedSink) -> FeedHandle:
        """Stream new documents to the sink.

        Attempts change streams first (requires replica set).
        Falls back to polling by inserted_at_field.
        """

        async def _change_stream_loop() -> None:
            """Watch for new inserts via MongoDB change streams."""
            try:
                coll = self._conn.collection
                pipeline = [{"$match": {"operationType": "insert"}}]
                if sub.subjects:
                    pipeline = [
                        {
                            "$match": {
                                "operationType": "insert",
                                f"fullDocument.{self._subject_field}": {
                                    "$in": list(sub.subjects)
                                },
                            }
                        }
                    ]

                def _watch():
                    return coll.watch(pipeline, full_document="updateLookup")

                stream = await asyncio.to_thread(_watch)
                logger.info("mongodb feed using change streams")

                while True:

                    def _next():
                        return stream.try_next()

                    change = await asyncio.to_thread(_next)
                    if change is not None:
                        doc = change.get("fullDocument", {})
                        record = _doc_to_record(
                            doc,
                            subject_field=self._subject_field,
                            timestamp_field=self._timestamp_field,
                            kind=sub.kind,
                            granularity=sub.granularity,
                        )
                        if record is not None:
                            await sink.on_record(record)
                    else:
                        await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.info(
                    "mongodb change streams unavailable (%s), falling back to polling",
                    exc,
                )
                await _polling_loop()

        async def _polling_loop() -> None:
            """Poll for new documents by inserted_at_field."""
            watermark: datetime | None = None
            logger.info(
                "mongodb feed polling every %.1fs by %s",
                self._poll_seconds,
                self._inserted_at_field,
            )

            while True:
                try:
                    coll = self._conn.collection
                    query: dict[str, Any] = {}
                    if sub.subjects:
                        query[self._subject_field] = {"$in": list(sub.subjects)}
                    if watermark is not None:
                        query[self._inserted_at_field] = {"$gt": watermark}

                    def _find():
                        return list(
                            coll.find(query).sort(self._inserted_at_field, 1).limit(100)
                        )

                    docs = await asyncio.to_thread(_find)

                    for doc in docs:
                        record = _doc_to_record(
                            doc,
                            subject_field=self._subject_field,
                            timestamp_field=self._timestamp_field,
                            kind=sub.kind,
                            granularity=sub.granularity,
                        )
                        if record is not None:
                            await sink.on_record(record)

                        # Update watermark
                        inserted_at = doc.get(self._inserted_at_field)
                        if isinstance(inserted_at, datetime):
                            if watermark is None or inserted_at > watermark:
                                watermark = inserted_at

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("mongodb polling error: %s", exc)

                await asyncio.sleep(max(0.5, self._poll_seconds))

        task = asyncio.create_task(_change_stream_loop())
        return _PollingFeedHandle(task)

    async def fetch(self, req: FeedFetchRequest) -> Sequence[FeedDataRecord]:
        """Query historical documents by timestamp range."""
        coll = self._conn.collection

        query: dict[str, Any] = {}
        if req.subjects:
            query[self._subject_field] = {"$in": list(req.subjects)}

        ts_filter: dict[str, Any] = {}
        if req.start_ts is not None:
            ts_filter["$gte"] = req.start_ts
        if req.end_ts is not None:
            ts_filter["$lte"] = req.end_ts
        if ts_filter:
            query[self._timestamp_field] = ts_filter

        limit = req.limit or 500

        def _query():
            return list(coll.find(query).sort(self._timestamp_field, -1).limit(limit))

        try:
            docs = await asyncio.to_thread(_query)
        except Exception as exc:
            logger.warning("mongodb fetch failed: %s", exc)
            return []

        records: list[FeedDataRecord] = []
        for doc in docs:
            record = _doc_to_record(
                doc,
                subject_field=self._subject_field,
                timestamp_field=self._timestamp_field,
                kind=req.kind,
                granularity=req.granularity,
            )
            if record is not None:
                records.append(record)

        # Return in chronological order (we queried desc for limit)
        records.reverse()
        return records


def build_mongodb_feed(settings: FeedSettings) -> MongoDBFeed:
    """Factory function for the feed registry."""
    return MongoDBFeed(settings)
