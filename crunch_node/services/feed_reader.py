from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from crunch_node.db.feed_records import DBFeedRecordRepository
from crunch_node.db.session import create_session
from crunch_node.entities.feed_record import FeedRecord
from crunch_node.feeds import FeedFetchRequest, create_default_registry
from crunch_node.feeds.normalizers import get_normalizer

if TYPE_CHECKING:
    from crunch_node.feeds.normalizers.base import FeedNormalizer

logger = logging.getLogger(__name__)


class FeedReader:
    """Reads feed data from DB, backfills from feed provider if needed."""

    def __init__(
        self,
        source: str = "binance",
        subjects: list[str] | None = None,
        kind: str = "candle",
        granularity: str = "1s",
        window_size: int = 120,
        *,
        subject: str | None = None,  # backward compat — single subject
        normalizer: FeedNormalizer | None = None,
    ):
        self.source = source
        if subjects:
            self.subjects = subjects
        elif subject:
            self.subjects = [subject]
        else:
            self.subjects = ["BTCUSDT"]
        self.subject = self.subjects[0]  # backward compat — primary subject
        self.kind = kind
        self.granularity = granularity
        self.window_size = window_size
        self._normalizer = normalizer or get_normalizer()

    @classmethod
    def from_env(cls) -> FeedReader:
        subjects_raw = os.getenv("FEED_SUBJECTS", os.getenv("FEED_ASSETS", "BTCUSDT"))
        subjects = [p.strip() for p in subjects_raw.split(",") if p.strip()] or [
            "BTCUSDT"
        ]
        return cls(
            source=os.getenv("FEED_SOURCE", os.getenv("FEED_PROVIDER", "binance"))
            .strip()
            .lower(),
            subjects=subjects,
            kind=os.getenv("FEED_KIND", "candle").strip().lower(),
            granularity=os.getenv("FEED_GRANULARITY", "1s").strip(),
            window_size=int(os.getenv("FEED_CANDLES_WINDOW", "120")),
        )

    def get_input(self, now: datetime) -> dict[str, Any]:
        """Build raw input dict for this timestep from recent feed records.

        Uses the configured normalizer to transform records into model input format.
        Also includes `_feed_timing` with timing data from the latest feed record
        for end-to-end latency measurement.
        """
        records, feed_timing = self._load_recent_records(limit=self.window_size)

        if len(records) < min(3, self.window_size):
            self._recover_window(
                start=now - timedelta(minutes=max(5, self.window_size)),
                end=now,
            )
            records, feed_timing = self._load_recent_records(limit=self.window_size)

        result = self._normalizer.normalize(
            records[-self.window_size :], self.subject
        ).model_dump()
        if feed_timing:
            result["_feed_timing"] = feed_timing
        return result

    def fetch_window(
        self,
        start: datetime,
        end: datetime,
        source: str | None = None,
        subject: str | None = None,
        kind: str | None = None,
        granularity: str | None = None,
    ) -> list[FeedRecord]:
        """Fetch feed records in a time window.

        Falls back to instance defaults for source/kind/granularity.
        When ``subject`` is None, fetches records for **all** configured
        subjects — this is the normal path for scoring, where
        ``resolve_ground_truth`` decides which records are relevant.
        """
        source = source or self.source
        kind = kind or self.kind
        granularity = granularity or self.granularity
        subjects = [subject] if subject else self.subjects

        records = self._fetch_subjects(source, subjects, kind, granularity, start, end)

        if not records:
            self._recover_window(
                start=start - timedelta(minutes=2), end=end + timedelta(minutes=2)
            )
            records = self._fetch_subjects(
                source, subjects, kind, granularity, start, end
            )

        # Sort by timestamp for consistent ordering across subjects
        records.sort(key=lambda r: self._ensure_utc(r.ts_event))
        return records

    def _fetch_subjects(
        self,
        source: str,
        subjects: list[str],
        kind: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[FeedRecord]:
        """Query feed records from DB for one or more subjects in a time window."""
        records: list[FeedRecord] = []
        for subj in subjects:
            with create_session() as session:
                repo = DBFeedRecordRepository(session)
                records.extend(
                    repo.fetch_records(
                        source=source,
                        subject=subj,
                        kind=kind,
                        granularity=granularity,
                        start_ts=self._ensure_utc(start),
                        end_ts=self._ensure_utc(end),
                    )
                )
        return records

    # ── internals ──

    def _load_recent_records(
        self, limit: int
    ) -> tuple[list[FeedRecord], dict[str, Any]]:
        with create_session() as session:
            repo = DBFeedRecordRepository(session)
            records = repo.fetch_records(
                source=self.source,
                subject=self.subject,
                kind=self.kind,
                granularity=self.granularity,
                limit=max(1, limit),
            )

        latest_timing: dict[str, Any] = {}
        if records:
            latest_record = records[-1]
            if latest_record.meta and latest_record.meta.get("timing"):
                latest_timing = latest_record.meta["timing"]

        return records, latest_timing

    def _latest_record(self, at_or_before: datetime) -> Any:
        with create_session() as session:
            repo = DBFeedRecordRepository(session)
            return repo.fetch_latest_record(
                source=self.source,
                subject=self.subject,
                kind=self.kind,
                granularity=self.granularity,
                at_or_before=self._ensure_utc(at_or_before),
            )

    def _recover_window(self, start: datetime, end: datetime) -> None:
        try:
            registry = create_default_registry()
            feed = registry.create(self.source)
            request = FeedFetchRequest(
                subjects=(self.subject,),
                kind=self.kind,
                granularity=self.granularity,
                start_ts=int(self._ensure_utc(start).timestamp()),
                end_ts=int(self._ensure_utc(end).timestamp()),
                limit=500,
            )
            records = self._run_async(feed.fetch(request))
        except Exception:
            return

        if not records:
            return

        with create_session() as session:
            repo = DBFeedRecordRepository(session)
            domain: list[FeedRecord] = []
            for row in records:
                ts_event = datetime.fromtimestamp(int(row.ts_event), tz=UTC)
                domain.append(
                    FeedRecord(
                        source=row.source or self.source,
                        subject=row.subject,
                        kind=row.kind,
                        granularity=row.granularity,
                        ts_event=ts_event,
                        values=dict(row.values),
                        meta=dict(row.metadata),
                        ts_ingested=datetime.now(UTC),
                    )
                )
            if domain:
                repo.append_records(domain)

    @staticmethod
    def _run_async(coro: Any) -> list:
        try:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(coro)
            if loop.is_running():
                try:
                    coro.close()
                except Exception:
                    pass
                return []
            return loop.run_until_complete(coro)
        except Exception:
            return []

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
