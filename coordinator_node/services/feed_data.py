from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from coordinator_node.entities.feed_record import FeedIngestionState, FeedRecord
from coordinator_node.feeds import (
    FeedDataRecord,
    FeedFetchRequest,
    FeedSubscription,
    create_default_registry,
)


@dataclass(frozen=True)
class FeedDataSettings:
    source: str
    subjects: tuple[str, ...]
    kind: str
    granularity: str
    poll_seconds: float
    backfill_minutes: int
    ttl_days: int
    retention_check_seconds: int

    @classmethod
    def from_env(cls) -> FeedDataSettings:
        subjects_raw = os.getenv("FEED_SUBJECTS", os.getenv("FEED_ASSETS", "BTC"))
        subjects = tuple(
            part.strip() for part in subjects_raw.split(",") if part.strip()
        )

        return cls(
            source=os.getenv("FEED_SOURCE", os.getenv("FEED_PROVIDER", "pyth"))
            .strip()
            .lower(),
            subjects=subjects or ("BTC",),
            kind=os.getenv("FEED_KIND", "tick").strip().lower(),
            granularity=os.getenv("FEED_GRANULARITY", "1s").strip(),
            poll_seconds=float(os.getenv("FEED_POLL_SECONDS", "5")),
            backfill_minutes=int(os.getenv("FEED_BACKFILL_MINUTES", "180")),
            ttl_days=int(
                os.getenv(
                    "FEED_RECORD_TTL_DAYS", os.getenv("MARKET_RECORD_TTL_DAYS", "90")
                )
            ),
            retention_check_seconds=int(
                os.getenv(
                    "FEED_RETENTION_CHECK_SECONDS",
                    os.getenv("MARKET_RETENTION_CHECK_SECONDS", "3600"),
                )
            ),
        )


class FeedDataService:
    def __init__(
        self,
        settings: FeedDataSettings,
        feed_record_repository,
    ):
        self.settings = settings
        self.feed_record_repository = feed_record_repository
        self.logger = logging.getLogger(__name__)
        self.stop_event = asyncio.Event()
        self._handles = []

    async def run(self) -> None:
        self.logger.info(
            "feed data service started source=%s subjects=%s kind=%s granularity=%s",
            self.settings.source,
            ",".join(self.settings.subjects),
            self.settings.kind,
            self.settings.granularity,
        )

        registry = create_default_registry()
        feed = registry.create_from_env(default_provider=self.settings.source)

        await self._backfill(feed)

        sink = _RepositorySink(self.feed_record_repository)
        subscription = FeedSubscription(
            subjects=self.settings.subjects,
            kind=self.settings.kind,
            granularity=self.settings.granularity,
        )
        handle = await feed.listen(subscription, sink)
        self._handles.append(handle)

        retention_task = asyncio.create_task(self._retention_loop())

        try:
            await self.stop_event.wait()
        finally:
            retention_task.cancel()
            for item in self._handles:
                try:
                    await item.stop()
                except Exception:
                    pass

    async def shutdown(self) -> None:
        self.stop_event.set()

    async def _backfill(self, feed) -> None:
        now = datetime.now(UTC)

        for subject in self.settings.subjects:
            watermark = self.feed_record_repository.get_watermark(
                source=self.settings.source,
                subject=subject,
                kind=self.settings.kind,
                granularity=self.settings.granularity,
            )

            start = (
                watermark.last_event_ts
                if watermark is not None and watermark.last_event_ts is not None
                else now - timedelta(minutes=max(1, self.settings.backfill_minutes))
            )

            req = FeedFetchRequest(
                subjects=(subject,),
                kind=self.settings.kind,
                granularity=self.settings.granularity,
                start_ts=int(start.timestamp()),
                end_ts=int(now.timestamp()),
                limit=500,
            )

            records = await feed.fetch(req)
            written = self._append_feed_records(records)
            if written:
                latest_ts = max(record.ts_event for record in records)
                self.feed_record_repository.set_watermark(
                    FeedIngestionState(
                        source=self.settings.source,
                        subject=subject,
                        kind=self.settings.kind,
                        granularity=self.settings.granularity,
                        last_event_ts=datetime.fromtimestamp(latest_ts, tz=UTC),
                        meta={"phase": "backfill"},
                    )
                )
                self.logger.info("backfill subject=%s wrote=%d", subject, written)

    async def _retention_loop(self) -> None:
        while not self.stop_event.is_set():
            cutoff = datetime.now(UTC) - timedelta(days=max(1, self.settings.ttl_days))
            try:
                deleted = self.feed_record_repository.prune_before(cutoff)
                if deleted:
                    self.logger.info(
                        "feed record retention pruned=%d cutoff=%s",
                        deleted,
                        cutoff.isoformat(),
                    )
            except Exception as exc:
                self.logger.warning("feed record retention failed: %s", exc)

            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=max(30, self.settings.retention_check_seconds),
                )
            except TimeoutError:
                pass

    def _append_feed_records(self, records: Sequence[FeedDataRecord]) -> int:
        if not records:
            return 0

        converted = [
            _feed_to_domain(self.settings.source, record) for record in records
        ]
        return self.feed_record_repository.append_records(converted)


class _RepositorySink:
    def __init__(self, repository, label: str = "feed"):
        self._repository = repository
        self._label = label
        self._ingest_count = 0
        self._logger = logging.getLogger(__name__)

    async def on_record(self, record: FeedDataRecord) -> None:
        domain = _feed_to_domain(record.source, record)
        self._repository.append_records([domain])
        self._ingest_count += 1
        if self._ingest_count % 10 == 0:
            self._logger.info(
                "%s ingested %d records (latest: subject=%s kind=%s)",
                self._label,
                self._ingest_count,
                record.subject,
                record.kind,
            )
        self._repository.set_watermark(
            FeedIngestionState(
                source=record.source,
                subject=record.subject,
                kind=record.kind,
                granularity=record.granularity,
                last_event_ts=datetime.fromtimestamp(record.ts_event, tz=UTC),
                meta={"phase": "listen"},
            )
        )
        try:
            from coordinator_node.db.pg_notify import notify

            notify("new_feed_data")
        except Exception:
            pass


def _feed_to_domain(default_source: str, record: FeedDataRecord) -> FeedRecord:
    source = record.source or default_source
    return FeedRecord(
        source=source,
        subject=record.subject,
        kind=record.kind,
        granularity=record.granularity,
        ts_event=datetime.fromtimestamp(int(record.ts_event), tz=UTC),
        values=dict(record.values),
        meta=dict(record.metadata),
    )
