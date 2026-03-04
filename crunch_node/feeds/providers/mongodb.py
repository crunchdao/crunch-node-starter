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
    FEED_OPT_timestamp_type=auto             # "datetime", "numeric", or "auto" (auto-detect)
    FEED_OPT_initial_lookback_seconds=0      # poll mode: 0=now, -1=all history, N=last N seconds

Listen modes:
- changestream: Uses MongoDB change streams (requires replica set / Atlas / sharded cluster)
- poll: Polls by inserted_at_field (works on any deployment including standalone, DocumentDB, CosmosDB)

Requires: pip install crunch-node[mongodb]
"""

from __future__ import annotations

import asyncio
import logging
import queue as queue_mod
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse, urlunparse

from crunch_node.feeds.base import DataFeed, FeedHandle, FeedSink
from crunch_node.feeds.contracts import (
    FeedDataRecord,
    FeedFetchRequest,
    FeedSubscription,
    SubjectDescriptor,
)
from crunch_node.feeds.registry import FeedSettings

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
_CHANGE_STREAM_UNSUPPORTED_CODES = frozenset(
    {
        40573,  # "$changeStream only supported on replica sets" (standalone MongoDB)
        40324,  # "Unrecognized pipeline stage '$changeStream'" (old MongoDB, FerretDB)
        303,  # "Change streams not supported" (AWS DocumentDB)
        115,  # "Command not supported" (Azure CosmosDB)
        160,  # "Unknown error" (CosmosDB variant)
    }
)

# MongoDB error codes that mean "permission denied" — permanent, user must fix config.
_AUTH_ERROR_CODES = frozenset(
    {
        13,  # Unauthorized
        18,  # AuthenticationFailed
    }
)

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


_FIELD_NAME_BANNED_CHARS = frozenset("${}  \t\n\r\0")


def _validate_field_name(name: str, label: str) -> None:
    """Validate that a MongoDB field name is safe for use in queries and aggregations.

    Rejects characters that could cause injection in aggregation expressions
    (``$``, ``{``, ``}``) and whitespace/null bytes. All other characters are
    allowed — including hyphens (``block-time``), dots for nested paths
    (``data.mint``), and unicode characters, which are all valid and common
    in real MongoDB collections.
    """
    if not name:
        raise ValueError(f"Empty {label} is not allowed.")
    if any(c in _FIELD_NAME_BANNED_CHARS for c in name):
        raise ValueError(
            f"Invalid {label}={name!r}. "
            f"Field names must not contain $, braces, or whitespace."
        )


def _make_json_safe(value: Any) -> Any:
    """Recursively convert a value to a JSON-serializable type.

    Handles BSON types like ObjectId, Decimal128, Binary, Regex by converting
    to str(). Recurses into dicts and lists to catch nested BSON values like
    {"refs": [ObjectId("...")]}.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _make_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_make_json_safe(v) for v in value]
    # Non-JSON BSON type — stringify
    return str(value)


def _get_nested(doc: dict[str, Any], path: str) -> Any:
    """Traverse a dotted field path in a plain dict.

    For example, _get_nested({"data": {"mint": "X"}}, "data.mint") returns "X".
    Returns None if any segment is missing or the intermediate value is not a dict.
    Non-dotted paths fall back to a simple dict.get().
    """
    if "." not in path:
        return doc.get(path)
    parts = path.split(".")
    current: Any = doc
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _to_watermark(value: Any) -> datetime | float | int | None:
    """Return the raw watermark value, preserving its original type.

    This is critical for poll-mode queries: the $gt comparison must use the
    same BSON type as the stored field. Comparing a datetime watermark against
    numeric values (or vice versa) produces incorrect results due to BSON type ordering.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return value
    return None


def _is_transient_error(exc: Exception) -> bool:
    """Return True if this is a transient network/connection error worth retrying."""
    if ConnectionFailure is not None and isinstance(exc, ConnectionFailure):
        return True
    if NetworkTimeout is not None and isinstance(exc, NetworkTimeout):
        return True
    if ServerSelectionTimeoutError is not None and isinstance(
        exc, ServerSelectionTimeoutError
    ):
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
    """Lazy MongoDB connection wrapper.

    Thread-safe: _connect() is guarded by a lock to prevent concurrent
    asyncio.to_thread calls from creating duplicate MongoClient instances.
    """

    def __init__(self, settings: FeedSettings):
        self._settings = settings
        self._client: Any | None = None
        self._collection: Any | None = None
        self._lock = threading.Lock()

    def _connect(self) -> Any:
        if MongoClient is None:
            raise ImportError(
                "pymongo is required for the mongodb feed provider. "
                "Install it with: pip install crunch-node[mongodb]"
            )

        if self._client is not None:
            return self._collection

        with self._lock:
            # Re-check after acquiring lock (another thread may have connected)
            if self._client is not None:
                return self._collection

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
        # Signal the background thread to exit (if change stream mode).
        # The thread checks stop_event between try_next() calls and will
        # exit cleanly within max_await_time_ms (~1s).
        if self._stop_event is not None:
            self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        # Only close the connection after the task (and its background thread)
        # have finished. Closing while a thread is mid-operation can cause
        # undefined behavior in PyMongo.
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
    subject = _get_nested(doc, subject_field)
    if subject is None:
        return None

    ts_raw = _get_nested(doc, timestamp_field)
    if ts_raw is None:
        return None

    # Handle both unix timestamps (int) and datetime objects
    if isinstance(ts_raw, datetime):
        ts_event = int(ts_raw.timestamp())
    elif isinstance(ts_raw, (int, float)):
        ts_event = int(ts_raw)
    else:
        return None

    # Build values dict — include all fields except internal MongoDB fields.
    # MongoDB documents can contain BSON types (ObjectId, Decimal128, Binary, etc.)
    # that are not JSON-serializable. Since these values end up in a PostgreSQL
    # JSONB column, we must ensure everything is JSON-safe — including nested structures.
    values: dict[str, Any] = {}
    for key, value in doc.items():
        if key.startswith("_"):
            continue
        values[key] = _make_json_safe(value)

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

        # Validate field names to prevent injection in query keys / aggregation expressions
        _validate_field_name(self._subject_field, "subject_field")
        _validate_field_name(self._timestamp_field, "timestamp_field")
        _validate_field_name(self._inserted_at_field, "inserted_at_field")

        # Validate listen_mode
        self._listen_mode = (
            _opt(settings, "listen_mode", "changestream").strip().lower()
        )
        if self._listen_mode not in _VALID_LISTEN_MODES:
            raise ValueError(
                f"Invalid FEED_OPT_listen_mode={self._listen_mode!r}. "
                f"Must be one of: {', '.join(_VALID_LISTEN_MODES)}"
            )

        # Timestamp type detection cache — None means "not yet detected".
        # Set by _detect_timestamp_type() on first fetch() call, or immediately
        # if FEED_OPT_timestamp_type is configured explicitly.
        self._timestamp_is_datetime: bool | None = None

        # Lock for one-time timestamp type detection (fetch path)
        self._ts_detect_lock = asyncio.Lock()

    async def list_subjects(self) -> Sequence[SubjectDescriptor]:
        """Discover subjects by querying distinct values of the subject field.

        Scopes to recent documents first (via $sort + $limit on _id) to avoid
        scanning the entire collection, then groups distinct subjects.
        Results are sorted alphabetically for deterministic output across runs.
        """
        coll = self._conn.collection
        # How many recent docs to sample for subject discovery.
        # This bounds the collection scan — we look at the most recent 10k docs
        # rather than grouping over millions.
        _SAMPLE_SIZE = 10_000

        def _fetch_subjects() -> list[Any]:
            pipeline = [
                # Scope to recent documents to avoid full-collection scan
                {"$sort": {"_id": -1}},
                {"$limit": _SAMPLE_SIZE},
                {"$group": {"_id": f"${self._subject_field}"}},
                # Sort for deterministic results across runs
                {"$sort": {"_id": 1}},
                {"$limit": self._subject_limit},
            ]
            return [doc["_id"] for doc in coll.aggregate(pipeline)]

        subjects = await asyncio.to_thread(_fetch_subjects)

        # Use configured kind/granularity so callers see the actual capabilities,
        # not hardcoded "event". Falls back to ("event",) if not configured.
        configured_kind = _opt(self.settings, "default_kind", "event")
        configured_granularity = _opt(self.settings, "default_granularity", "event")

        descriptors: list[SubjectDescriptor] = []
        for subject in subjects:
            descriptors.append(
                SubjectDescriptor(
                    symbol=str(subject),
                    display_name=str(subject),
                    kinds=(configured_kind,),
                    granularities=(configured_granularity,),
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

        watch_task: asyncio.Future[None] | None = None

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
                # Use stdlib queue.Queue (thread-safe) — asyncio.Queue is NOT
                # thread-safe and must not be used from background threads.
                # Bounded queue provides backpressure — if the consumer is slow,
                # the producer thread blocks rather than growing unbounded.
                doc_queue: queue_mod.Queue[dict[str, Any]] = queue_mod.Queue(
                    maxsize=1000
                )

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
                                    # put() with timeout so we can check stop_event
                                    while not stop_event.is_set():
                                        try:
                                            doc_queue.put(doc, timeout=0.5)
                                            break
                                        except queue_mod.Full:
                                            continue

                # Start the blocking watch loop in a background thread
                loop = asyncio.get_running_loop()
                watch_task = loop.run_in_executor(None, _watch_and_iterate)

                logger.info(
                    "mongodb feed using change streams (listen_mode=changestream)"
                )

                # Don't reset error counters yet — wait until the watch cursor
                # is confirmed working (first document or successful try_next).
                stream_confirmed = False

                # Consume documents from the thread-safe queue.
                # Use to_thread(doc_queue.get, timeout=...) to avoid blocking
                # the event loop while waiting for documents.
                while True:
                    # Check if the watch thread died
                    if watch_task.done():
                        # Re-raise any exception from the watch thread
                        watch_task.result()
                        # Thread exited cleanly (stop_event was set)
                        return

                    try:
                        doc = await asyncio.to_thread(doc_queue.get, timeout=1.0)
                    except queue_mod.Empty:
                        # No new docs but stream is alive — count as confirmed.
                        if not stream_confirmed:
                            stream_confirmed = True
                            consecutive_errors = 0
                            backoff = _INITIAL_BACKOFF_SECONDS
                        continue

                    # First document received — stream is definitely working
                    if not stream_confirmed:
                        stream_confirmed = True
                        consecutive_errors = 0
                        backoff = _INITIAL_BACKOFF_SECONDS

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
                if watch_task is not None and not watch_task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(watch_task), timeout=5.0)
                    except (TimeoutError, asyncio.CancelledError, Exception):
                        pass
                raise

            except Exception as exc:
                # Signal the thread to stop and wait for it to exit.
                # Keep stop_event set until we know we're retrying.
                stop_event.set()
                if watch_task is not None and not watch_task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(watch_task), timeout=5.0)
                    except (TimeoutError, asyncio.CancelledError, Exception):
                        pass

                # Classify the error — decide retry vs propagate
                if OperationFailure is not None and isinstance(exc, OperationFailure):
                    code = exc.code

                    if code in _CHANGE_STREAM_UNSUPPORTED_CODES:
                        # Permanent — don't clear stop_event, thread should stay dead
                        raise RuntimeError(
                            f"Change streams are not supported by your MongoDB deployment "
                            f"(error code {code}: {exc}). "
                            f"Set FEED_OPT_listen_mode=poll to use polling instead."
                        ) from exc

                    if code in _AUTH_ERROR_CODES:
                        # Permanent — don't clear stop_event
                        raise RuntimeError(
                            f"MongoDB authentication/authorization failed (error code {code}: {exc}). "
                            f"Check your FEED_OPT_mongodb_uri credentials and database permissions."
                        ) from exc

                # Transient error — retry with backoff
                if _is_transient_error(exc):
                    consecutive_errors += 1
                    if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                        # Giving up — don't clear stop_event
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
                    # Only clear stop_event once the old thread has exited.
                    # If the thread is still alive after the 5s timeout above,
                    # it will see the set event on its next try_next() cycle
                    # and exit. We must wait for that before starting a new thread
                    # to avoid zombie threads pushing into orphaned queues.
                    if watch_task is not None and not watch_task.done():
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(watch_task), timeout=10.0
                            )
                        except (TimeoutError, Exception):
                            logger.warning(
                                "mongodb change stream: old thread did not exit within timeout. "
                                "Proceeding with retry — orphaned thread may leak."
                            )
                    stop_event.clear()
                    continue

                # Unknown/unexpected error — don't clear stop_event, let thread die
                raise

    async def _polling_loop(
        self, sub: FeedSubscription, sink: FeedSink, conn: _MongoConnection
    ) -> None:
        """Poll for new documents by inserted_at_field.

        On first startup, respects FEED_OPT_initial_lookback_seconds to avoid
        replaying the entire collection history. Defaults to 0 (start from now).
        Set to a large value or -1 to process all historical data.
        """
        # Determine initial watermark to avoid replaying entire collection.
        # Must match the BSON type of inserted_at_field — datetime watermark
        # against numeric field (or vice versa) gives wrong results.
        lookback = int(_opt(self.settings, "initial_lookback_seconds", "0"))
        if lookback < 0:
            # Negative = process all history
            watermark: datetime | float | int | None = None
        else:
            now = datetime.now(UTC)
            start = now if lookback == 0 else now - timedelta(seconds=lookback)

            # Detect inserted_at field type to set watermark in matching BSON type
            def _detect_watermark_type() -> datetime | float | int:
                coll = conn.collection
                sample = coll.find_one(
                    {self._inserted_at_field: {"$exists": True}},
                    projection={self._inserted_at_field: 1},
                )
                if sample is not None:
                    val = _get_nested(sample, self._inserted_at_field)
                    if isinstance(val, (int, float)):
                        # Field stores numeric unix timestamps
                        return int(start.timestamp())
                # Default to datetime (works for BSON datetime fields and empty collections)
                return start

            watermark = await asyncio.to_thread(_detect_watermark_type)

            if lookback == 0:
                logger.info(
                    "mongodb poll: starting from now (watermark=%s, type=%s). "
                    "Set FEED_OPT_initial_lookback_seconds to process historical data.",
                    watermark,
                    type(watermark).__name__,
                )
            else:
                logger.info(
                    "mongodb poll: starting from %s (%ds lookback, type=%s)",
                    watermark,
                    lookback,
                    type(watermark).__name__,
                )
        # Track _ids seen at the current watermark value to handle
        # multiple documents with identical inserted_at timestamps.
        # Using $gte (not $gt) ensures we don't skip docs that share
        # the same timestamp when they span across poll batches.
        seen_ids_at_watermark: set[Any] = set()
        consecutive_errors = 0
        backoff = _INITIAL_BACKOFF_SECONDS
        # Base batch size — increased dynamically if watermark gets stuck
        batch_limit = 100
        # When True, use $gt instead of $gte to force past a stuck timestamp
        force_gt_next = False

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
                    op = "$gt" if force_gt_next else "$gte"
                    query[self._inserted_at_field] = {op: watermark}

                # Snapshot query dict and coll into closure defaults so they
                # are not affected by reassignment in the next loop iteration.
                inserted_at_field = self._inserted_at_field
                current_limit = batch_limit

                def _find(
                    q=dict(query), c=coll, f=inserted_at_field, lim=current_limit
                ):
                    return list(c.find(q).sort(f, 1).limit(lim))

                docs = await asyncio.to_thread(_find)

                watermark_before = watermark
                emitted_without_watermark = 0
                new_docs_count = 0

                for doc in docs:
                    doc_id = doc.get("_id")

                    # Skip documents we already processed at this watermark
                    if doc_id is not None and doc_id in seen_ids_at_watermark:
                        continue

                    new_docs_count += 1
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
                    inserted_at = _get_nested(doc, self._inserted_at_field)
                    new_wm = _to_watermark(inserted_at)
                    if new_wm is not None:
                        try:
                            advanced = watermark is None or new_wm > watermark
                        except TypeError:
                            # Mixed types (e.g. datetime vs int after schema migration).
                            # Can't compare — log and skip watermark update for this doc.
                            logger.warning(
                                "mongodb poll: watermark type mismatch — current watermark "
                                "is %s (%s) but document has %s (%s). Skipping watermark "
                                "update. This usually means inserted_at_field '%s' has "
                                "inconsistent types across documents.",
                                watermark,
                                type(watermark).__name__,
                                new_wm,
                                type(new_wm).__name__,
                                self._inserted_at_field,
                            )
                            advanced = False

                        if advanced:
                            # Watermark advanced — reset seen set for new value
                            watermark = new_wm
                            seen_ids_at_watermark = set()
                        # Track this doc's _id at the current watermark
                        if doc_id is not None:
                            seen_ids_at_watermark.add(doc_id)
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

                # Detect stuck watermark: batch was full but watermark didn't advance.
                # This means >batch_limit docs share the same inserted_at value.
                # Double the limit so the next poll can drain them all.
                _MAX_SEEN_IDS = 50_000

                if (
                    len(docs) >= batch_limit
                    and watermark == watermark_before
                    and watermark is not None
                ):
                    if len(seen_ids_at_watermark) >= _MAX_SEEN_IDS:
                        # Safety valve: too many docs at one timestamp.
                        # Force past this timestamp by using $gt (not $gte) next poll.
                        # This may skip remaining unseen docs at this exact timestamp.
                        logger.critical(
                            "mongodb poll: %d+ documents share timestamp %s, exceeding "
                            "dedup capacity (%d). Forcing watermark past this timestamp. "
                            "Some documents at this timestamp may be skipped. "
                            "Use a higher-granularity inserted_at_field to prevent this.",
                            len(seen_ids_at_watermark),
                            watermark,
                            _MAX_SEEN_IDS,
                        )
                        # Clear seen set and set force_gt flag for next query
                        seen_ids_at_watermark = set()
                        force_gt_next = True
                    else:
                        batch_limit = min(batch_limit * 2, 10_000)
                        logger.warning(
                            "mongodb poll: watermark stuck at %s with %d docs at same timestamp. "
                            "Increasing batch limit to %d to drain backlog. Consider using a "
                            "higher-granularity inserted_at_field to avoid this.",
                            watermark,
                            len(seen_ids_at_watermark),
                            batch_limit,
                        )
                        force_gt_next = False
                elif new_docs_count > 0:
                    # Watermark advanced — reset batch limit to normal
                    batch_limit = 100
                    force_gt_next = False
                else:
                    force_gt_next = False

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
        """Determine whether the timestamp field stores BSON datetimes or numeric values.

        If FEED_OPT_timestamp_type is set to "datetime" or "numeric", uses that directly.
        Otherwise, auto-detects by sampling one document and logs a warning that detection
        is based on a single sample (collections with mixed types may behave incorrectly).

        Uses an asyncio.Lock so concurrent fetch() calls don't race on detection.
        Returns True if the field stores datetimes, False for numeric values.
        """
        # Fast path — already cached
        if self._timestamp_is_datetime is not None:
            return self._timestamp_is_datetime

        async with self._ts_detect_lock:
            # Re-check after acquiring lock (another coroutine may have set it)
            if self._timestamp_is_datetime is not None:
                return self._timestamp_is_datetime

            # Check for explicit config first
            explicit_type = _opt(self.settings, "timestamp_type", "").strip().lower()
            if explicit_type == "datetime":
                self._timestamp_is_datetime = True
                logger.info("mongodb timestamp_type=datetime (explicit config)")
                return True
            elif explicit_type == "numeric":
                self._timestamp_is_datetime = False
                logger.info("mongodb timestamp_type=numeric (explicit config)")
                return False
            elif explicit_type and explicit_type != "auto":
                raise ValueError(
                    f"Invalid FEED_OPT_timestamp_type={explicit_type!r}. "
                    f"Must be 'datetime', 'numeric', or 'auto'."
                )

            # Auto-detect from a single sample document
            coll = self._conn.collection
            ts_field = self._timestamp_field

            def _detect() -> bool | None:
                sample = coll.find_one(
                    {ts_field: {"$exists": True}},
                    projection={ts_field: 1},
                )
                if sample is not None:
                    return isinstance(_get_nested(sample, ts_field), datetime)
                return None  # No documents — can't determine type yet

            result = await asyncio.to_thread(_detect)
            if result is None:
                # Collection is empty or has no documents with the timestamp field.
                # Default to numeric but don't cache — re-detect on next call
                # when documents may exist.
                logger.info(
                    "mongodb timestamp_type: no documents found with field '%s'. "
                    "Defaulting to numeric. Will re-detect on next fetch() call. "
                    "Set FEED_OPT_timestamp_type explicitly to avoid this.",
                    ts_field,
                )
                return False

            self._timestamp_is_datetime = result
            detected = "datetime" if result else "numeric"
            logger.warning(
                "mongodb timestamp_type auto-detected as '%s' from a single document sample. "
                "If your collection has mixed types (e.g. after a schema migration), "
                "set FEED_OPT_timestamp_type explicitly to avoid incorrect query results.",
                detected,
            )
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
            return list(coll.find(q).sort(self._timestamp_field, -1).limit(limit))

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
