"""Generic MongoDB feed provider.

Connects to any MongoDB collection and exposes it as a feed source.
All field mapping is configurable via FEED_OPT_* environment variables:

    FEED_PROVIDER=mongodb
    FEED_OPT_mongodb_uri=mongodb://user:pass@host:27017/?...
    FEED_OPT_database=my_database
    FEED_OPT_collection=my_collection
    FEED_OPT_timestamp_field=blockTime       # field for time ordering (unix seconds)
    FEED_OPT_subject_field=mint              # field used as subject identifier
    FEED_OPT_listen_mode=changestream        # "changestream" or "poll"
    FEED_OPT_poll_seconds=5                  # polling interval (poll mode)
    FEED_OPT_inserted_at_field=insertedAt    # field for tailing new documents (poll mode)

Listen modes:
- changestream: Uses MongoDB change streams (requires replica set / Atlas / sharded cluster)
- poll: Polls by inserted_at_field (works on any deployment including standalone, DocumentDB, CosmosDB)

Requires: pip install coordinator-node[mongodb]
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

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
    from pymongo.errors import (
        ConnectionFailure,
        NetworkTimeout,
        OperationFailure,
        ServerSelectionTimeoutError,
    )
except ImportError:
    MongoClient = None  # type: ignore[assignment,misc]
    OperationFailure = None  # type: ignore[assignment,misc]
    ConnectionFailure = None  # type: ignore[assignment,misc]
    NetworkTimeout = None  # type: ignore[assignment,misc]
    ServerSelectionTimeoutError = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Maximum consecutive errors before raising in polling/listen loops
_MAX_CONSECUTIVE_ERRORS = 10
_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0

# MongoDB error codes that mean "change streams are not supported on this deployment".
# These are permanent — retrying will never help, the user must switch to poll mode.
_CHANGE_STREAM_UNSUPPORTED_CODES = frozenset({
    40573,  # "$changeStream only supported on replica sets" (standalone MongoDB)
    40324,  # "Unrecognized pipeline stage '$changeStream'" (old MongoDB, FerretDB)
    303,    # "Change streams not supported" (AWS DocumentDB)
    115,    # "Command not supported" (Azure CosmosDB)
    160,    # "Unknown error" (CosmosDB variant)
})

# MongoDB error codes that mean "permission denied" — permanent, user must fix config.
_AUTH_ERROR_CODES = frozenset({
    13,     # Unauthorized
    18,     # AuthenticationFailed
})

# Valid listen_mode values
_VALID_LISTEN_MODES = ("changestream", "poll")


def _require_opt(settings: FeedSettings, key: str) -> str:
    """Read a required option from settings. Raises if missing."""
    value = settings.options.get(key)
    if not value:
        raise ValueError(
            f"Missing required FEED_OPT_{key} for mongodb provider. "
            f"Set it via environment variable FEED_OPT_{key}."
        )
    return value


def _opt(settings: FeedSettings, key: str, default: str) -> str:
    """Read an optional setting with an explicit default."""
    return settings.options.get(key, default)


def _redact_uri(uri: str) -> str:
    """Redact credentials from a MongoDB URI for safe logging."""
    try:
        parsed = urlparse(uri)
        if parsed.username or parsed.password:
            netloc = f"***:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
        return uri
    except Exception:
        return "<unparseable-uri>"


def _to_watermark(value: Any) -> datetime | None:
    """Convert a document field value to a datetime watermark.

    Handles both datetime objects and numeric unix timestamps.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    return None


def _is_transient_error(exc: Exception) -> bool:
    """Return True if this is a transient network/connection error worth retrying."""
    if ConnectionFailure is not None and isinstance(exc, ConnectionFailure):
        return True
    if NetworkTimeout is not None and isinstance(exc, NetworkTimeout):
        return True
    if ServerSelectionTimeoutError is not None and isinstance(exc, ServerSelectionTimeoutError):
        return True
    if OperationFailure is not None and isinstance(exc, OperationFailure):
        # Network-level operation failures (e.g. cursor lost) are transient
        # but "not supported" and "auth" codes are permanent
        code = exc.code
        if code in _CHANGE_STREAM_UNSUPPORTED_CODES or code in _AUTH_ERROR_CODES:
            return False
        return True
    return False


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
                "Install it with: pip install coordinator-node[mongodb]"
            )

        if self._client is None:
            uri = _require_opt(self._settings, "mongodb_uri")
            db_name = _require_opt(self._settings, "database")
            coll_name = _require_opt(self._settings, "collection")

            self._client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
            db = self._client[db_name]
            self._collection = db[coll_name]
            logger.info(
                "mongodb feed connected to %s.%s (uri: %s)",
                db_name,
                coll_name,
                _redact_uri(uri),
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


class _MongoFeedHandle:
    """Feed handle that cleans up the async task, background thread, and MongoDB connection."""

    def __init__(
        self,
        task: asyncio.Task[None],
        conn: _MongoConnection,
        stop_event: threading.Event | None = None,
    ):
        self._task = task
        self._conn = conn
        self._stop_event = stop_event

    async def stop(self) -> None:
        # Signal the background thread to exit (if change stream mode)
        if self._stop_event is not None:
            self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        # Close the MongoDB connection to avoid leaking sockets.
        # This also interrupts any blocking MongoDB operation in the thread,
        # causing it to raise and exit.
        self._conn.close()


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
        # Validate all required options eagerly at construction time
        _require_opt(settings, "mongodb_uri")
        _require_opt(settings, "database")
        _require_opt(settings, "collection")
        self._conn = _MongoConnection(settings)
        self._subject_field = _require_opt(settings, "subject_field")
        self._timestamp_field = _require_opt(settings, "timestamp_field")
        self._inserted_at_field = _opt(settings, "inserted_at_field", "insertedAt")
        self._poll_seconds = float(_opt(settings, "poll_seconds", "5"))
        self._subject_limit = int(_opt(settings, "subject_limit", "500"))

        # Validate listen_mode
        self._listen_mode = _opt(settings, "listen_mode", "changestream").strip().lower()
        if self._listen_mode not in _VALID_LISTEN_MODES:
            raise ValueError(
                f"Invalid FEED_OPT_listen_mode={self._listen_mode!r}. "
                f"Must be one of: {', '.join(_VALID_LISTEN_MODES)}"
            )

    async def list_subjects(self) -> Sequence[SubjectDescriptor]:
        """Discover subjects by querying distinct values of the subject field.

        Uses an aggregation pipeline with $limit to bound server-side work,
        rather than loading all distinct values into memory.
        """
        coll = self._conn.collection

        def _fetch_subjects() -> list[Any]:
            pipeline = [
                {"$group": {"_id": f"${self._subject_field}"}},
                {"$limit": self._subject_limit},
            ]
            return [doc["_id"] for doc in coll.aggregate(pipeline)]

        subjects = await asyncio.to_thread(_fetch_subjects)

        descriptors: list[SubjectDescriptor] = []
        for subject in subjects:
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

        Each call creates its own MongoDB connection so that multiple handles
        can be stopped independently without killing each other's connections.

        Uses the configured listen_mode:
        - "changestream": MongoDB change streams (requires replica set)
        - "poll": Polls by inserted_at_field (works on any deployment)
        """
        # Each handle owns its own connection to avoid shared-state issues
        conn = _MongoConnection(self.settings)
        stop_event = threading.Event()

        if self._listen_mode == "changestream":
            task = asyncio.create_task(
                self._change_stream_loop(sub, sink, conn, stop_event)
            )
        else:
            task = asyncio.create_task(self._polling_loop(sub, sink, conn))
            stop_event = None  # polling doesn't use a background thread

        return _MongoFeedHandle(task, conn, stop_event)

    async def _change_stream_loop(
        self,
        sub: FeedSubscription,
        sink: FeedSink,
        conn: _MongoConnection,
        stop_event: threading.Event,
    ) -> None:
        """Watch for new inserts via MongoDB change streams.

        Retries on transient network errors. Raises on permanent errors
        (unsupported deployment, auth failures) with actionable messages.

        The blocking watch loop runs in a single dedicated thread and checks
        stop_event between iterations so it can be cleanly shut down.
        """
        consecutive_errors = 0
        backoff = _INITIAL_BACKOFF_SECONDS

        while True:
            try:
                coll = conn.collection
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

                # Run the entire watch + iteration loop in a single thread
                # to avoid thread-safety issues with PyMongo cursors.
                queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

                def _watch_and_iterate() -> None:
                    """Blocking loop: watch change stream, push docs to queue.

                    Uses max_await_time_ms so the cursor returns periodically,
                    allowing us to check the stop_event between iterations.
                    """
                    with coll.watch(
                        pipeline,
                        full_document="updateLookup",
                        max_await_time_ms=1000,
                    ) as stream:
                        while not stop_event.is_set():
                            change = stream.try_next()
                            if change is not None:
                                doc = change.get("fullDocument", {})
                                if doc:
                                    queue.put_nowait(doc)

                # Start the blocking watch loop in a background thread
                loop = asyncio.get_running_loop()
                watch_task = loop.run_in_executor(None, _watch_and_iterate)

                logger.info(
                    "mongodb feed using change streams (listen_mode=changestream)"
                )

                # Reset error state on successful connection
                consecutive_errors = 0
                backoff = _INITIAL_BACKOFF_SECONDS

                # Consume documents from the queue
                while True:
                    # Check if the watch thread died
                    if watch_task.done():
                        # Re-raise any exception from the watch thread
                        watch_task.result()
                        # Thread exited cleanly (stop_event was set)
                        return

                    try:
                        doc = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except TimeoutError:
                        continue

                    record = _doc_to_record(
                        doc,
                        subject_field=self._subject_field,
                        timestamp_field=self._timestamp_field,
                        kind=sub.kind,
                        granularity=sub.granularity,
                    )
                    if record is not None:
                        await sink.on_record(record)

            except asyncio.CancelledError:
                # Signal the thread to stop and wait for it
                stop_event.set()
                if "watch_task" in locals() and not watch_task.done():
                    # Give the thread a moment to exit cleanly
                    try:
                        await asyncio.wait_for(asyncio.shield(watch_task), timeout=5.0)
                    except (TimeoutError, asyncio.CancelledError, Exception):
                        pass
                raise

            except Exception as exc:
                # Signal the old thread to stop before retrying
                stop_event.set()
                if "watch_task" in locals() and not watch_task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(watch_task), timeout=5.0)
                    except (TimeoutError, asyncio.CancelledError, Exception):
                        pass
                # Reset the event for the next iteration
                stop_event.clear()

                # Classify the error
                if OperationFailure is not None and isinstance(exc, OperationFailure):
                    code = exc.code

                    if code in _CHANGE_STREAM_UNSUPPORTED_CODES:
                        raise RuntimeError(
                            f"Change streams are not supported by your MongoDB deployment "
                            f"(error code {code}: {exc}). "
                            f"Set FEED_OPT_listen_mode=poll to use polling instead."
                        ) from exc

                    if code in _AUTH_ERROR_CODES:
                        raise RuntimeError(
                            f"MongoDB authentication/authorization failed (error code {code}: {exc}). "
                            f"Check your FEED_OPT_mongodb_uri credentials and database permissions."
                        ) from exc

                # Transient error — retry with backoff
                if _is_transient_error(exc):
                    consecutive_errors += 1
                    if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError(
                            f"mongodb change stream failed {consecutive_errors} consecutive times, "
                            f"last error: {exc}"
                        ) from exc

                    logger.warning(
                        "mongodb change stream transient error (%d/%d): %s — retrying in %.1fs",
                        consecutive_errors,
                        _MAX_CONSECUTIVE_ERRORS,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                    continue

                # Unknown/unexpected error — don't swallow it
                raise

    async def _polling_loop(
        self, sub: FeedSubscription, sink: FeedSink, conn: _MongoConnection
    ) -> None:
        """Poll for new documents by inserted_at_field."""
        watermark: datetime | None = None
        consecutive_errors = 0
        backoff = _INITIAL_BACKOFF_SECONDS

        logger.info(
            "mongodb feed polling every %.1fs by %s (listen_mode=poll)",
            self._poll_seconds,
            self._inserted_at_field,
        )

        while True:
            try:
                coll = conn.collection
                query: dict[str, Any] = {}
                if sub.subjects:
                    query[self._subject_field] = {"$in": list(sub.subjects)}
                if watermark is not None:
                    query[self._inserted_at_field] = {"$gt": watermark}

                # Snapshot the query dict so the closure is not affected by
                # mutations in the next loop iteration.
                def _find(q=dict(query)):
                    return list(
                        coll.find(q).sort(self._inserted_at_field, 1).limit(100)
                    )

                docs = await asyncio.to_thread(_find)

                emitted_without_watermark = 0
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

                    # Update watermark — handles both datetime and numeric timestamps
                    inserted_at = doc.get(self._inserted_at_field)
                    new_wm = _to_watermark(inserted_at)
                    if new_wm is not None:
                        if watermark is None or new_wm > watermark:
                            watermark = new_wm
                    else:
                        emitted_without_watermark += 1

                if emitted_without_watermark > 0:
                    logger.warning(
                        "mongodb poll: %d documents missing or have unsupported type "
                        "for inserted_at_field '%s' — watermark cannot advance for these, "
                        "which may cause duplicate ingestion. Ensure all documents have "
                        "this field as a datetime or numeric unix timestamp.",
                        emitted_without_watermark,
                        self._inserted_at_field,
                    )

                # Reset error state on success
                consecutive_errors = 0
                backoff = _INITIAL_BACKOFF_SECONDS

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                # Auth errors are permanent
                if OperationFailure is not None and isinstance(exc, OperationFailure):
                    if exc.code in _AUTH_ERROR_CODES:
                        raise RuntimeError(
                            f"MongoDB authentication/authorization failed (error code {exc.code}: {exc}). "
                            f"Check your FEED_OPT_mongodb_uri credentials and database permissions."
                        ) from exc

                # Transient errors — retry with backoff
                if _is_transient_error(exc):
                    consecutive_errors += 1
                    if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        logger.critical(
                            "mongodb polling failed %d consecutive times, last error: %s",
                            consecutive_errors,
                            exc,
                        )
                        raise RuntimeError(
                            f"mongodb polling failed {consecutive_errors} consecutive times"
                        ) from exc
                    logger.warning(
                        "mongodb polling error (%d/%d): %s — retrying in %.1fs",
                        consecutive_errors,
                        _MAX_CONSECUTIVE_ERRORS,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                    continue

                # Unknown/unexpected error — don't swallow it
                raise

            await asyncio.sleep(max(0.5, self._poll_seconds))

    async def _detect_timestamp_type(self) -> bool:
        """Detect whether the timestamp field stores BSON datetimes or numeric values.

        Runs the detection query in a thread and caches the result on the instance.
        Returns True if the field stores datetimes, False for numeric values.
        """
        if hasattr(self, "_timestamp_is_datetime"):
            return self._timestamp_is_datetime

        coll = self._conn.collection
        ts_field = self._timestamp_field

        def _detect() -> bool:
            sample = coll.find_one(
                {ts_field: {"$exists": True}},
                projection={ts_field: 1},
            )
            if sample is not None:
                return isinstance(sample.get(ts_field), datetime)
            return False

        result = await asyncio.to_thread(_detect)
        self._timestamp_is_datetime = result
        return result

    async def fetch(self, req: FeedFetchRequest) -> Sequence[FeedDataRecord]:
        """Query historical documents by timestamp range.

        Handles both numeric unix timestamps and BSON datetime fields
        by detecting the field type on first use.
        """
        coll = self._conn.collection

        query: dict[str, Any] = {}
        if req.subjects:
            query[self._subject_field] = {"$in": list(req.subjects)}

        ts_filter: dict[str, Any] = {}
        if req.start_ts is not None:
            ts_filter["$gte"] = req.start_ts
        if req.end_ts is not None:
            ts_filter["$lte"] = req.end_ts

        limit = req.limit or 500

        # Detect timestamp field type on the main thread (cached after first call)
        if ts_filter:
            timestamp_is_datetime = await self._detect_timestamp_type()

            # Convert numeric unix-second bounds to BSON datetimes if needed
            if timestamp_is_datetime:
                converted: dict[str, Any] = {}
                if "$gte" in ts_filter:
                    converted["$gte"] = datetime.fromtimestamp(
                        ts_filter["$gte"], tz=UTC
                    )
                if "$lte" in ts_filter:
                    converted["$lte"] = datetime.fromtimestamp(
                        ts_filter["$lte"], tz=UTC
                    )
                ts_filter = converted

        # Build final query — snapshot into closure to avoid mutation races
        final_query = dict(query)
        if ts_filter:
            final_query[self._timestamp_field] = ts_filter

        def _query(q=dict(final_query)):
            return list(
                coll.find(q)
                .sort(self._timestamp_field, -1)
                .limit(limit)
            )

        docs = await asyncio.to_thread(_query)

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
