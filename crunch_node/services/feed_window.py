"""In-memory rolling window of feed records for low-latency prediction."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from typing import Any

from crunch_node.feeds import FeedDataRecord


class FeedWindow:
    """Maintains a rolling window of recent feed records per subject.

    Used by the combined feed-predict worker to avoid DB queries on the hot path.
    On startup, call load_from_db() to initialize from existing records.
    """

    def __init__(self, max_size: int = 120):
        self._windows: dict[str, deque[FeedDataRecord]] = {}
        self._max_size = max_size

    def append(self, record: FeedDataRecord) -> None:
        subject = record.subject
        if subject not in self._windows:
            self._windows[subject] = deque(maxlen=self._max_size)
        self._windows[subject].append(record)

    def get_candles(self, subject: str) -> list[dict[str, Any]]:
        """Return candle-format dicts for the given subject."""
        window = self._windows.get(subject)
        if not window:
            return []

        candles: list[dict[str, Any]] = []
        for record in window:
            candle = self._record_to_candle(record)
            if candle:
                candles.append(candle)
        return candles

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

    def _record_to_candle(self, record: FeedDataRecord) -> dict[str, Any] | None:
        values = record.values or {}
        price = self._extract_price(values)
        if price is None:
            return None

        ts_event = int(record.ts_event)

        if record.kind == "candle":
            return {
                "ts": ts_event,
                "open": float(values.get("open", price)),
                "high": float(values.get("high", price)),
                "low": float(values.get("low", price)),
                "close": float(values.get("close", price)),
                "volume": float(values.get("volume", 0.0)),
            }
        else:
            return {
                "ts": ts_event,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
            }

    @staticmethod
    def _extract_price(values: dict[str, Any]) -> float | None:
        for key in ("close", "price"):
            if key in values:
                try:
                    return float(values[key])
                except (TypeError, ValueError):
                    return None
        return None
