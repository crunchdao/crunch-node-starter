"""In-memory rolling window of feed records for low-latency prediction."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from crunch_node.feeds import FeedDataRecord
from crunch_node.feeds.normalizers import get_normalizer

if TYPE_CHECKING:
    from crunch_node.feeds.normalizers.base import FeedNormalizer


class FeedWindow:
    """Maintains a rolling window of recent feed records per subject.

    Used by the combined feed-predict worker to avoid DB queries on the hot path.
    On startup, call load_from_db() to initialize from existing records.
    """

    def __init__(
        self,
        max_size: int = 120,
        normalizer: FeedNormalizer | None = None,
    ):
        self._windows: dict[str, deque[FeedDataRecord]] = {}
        self._max_size = max_size
        self._normalizer = normalizer or get_normalizer()

    def append(self, record: FeedDataRecord) -> None:
        subject = record.subject
        if subject not in self._windows:
            self._windows[subject] = deque(maxlen=self._max_size)
        self._windows[subject].append(record)

    def get_input(self, subject: str) -> dict[str, Any]:
        """Return normalized input for the given subject."""
        records = list(self._windows.get(subject, []))
        return self._normalizer.normalize(records, subject).model_dump()

    def get_latest_ts(self, subject: str) -> int:
        """Return the timestamp of the most recent record for subject."""
        window = self._windows.get(subject)
        if not window:
            return 0
        return int(window[-1].ts_event)

    def load_from_db(self, repository, settings) -> None:
        """Initialize windows from database on startup."""
        for subject in settings.subjects:
            records = repository.fetch_records(
                source=settings.source,
                subject=subject,
                kind=settings.kind,
                granularity=settings.granularity,
                limit=self._max_size,
            )
            self._windows[subject] = deque(maxlen=self._max_size)
            for record in records:
                feed_record = FeedDataRecord(
                    source=record.source,
                    subject=record.subject,
                    kind=record.kind,
                    granularity=record.granularity,
                    ts_event=int(record.ts_event.timestamp()),
                    values=record.values or {},
                    metadata=record.meta or {},
                )
                self._windows[subject].append(feed_record)
