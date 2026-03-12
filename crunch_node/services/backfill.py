"""Paginated historical backfill service for data feeds."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from crunch_node.db.protocols import FeedRecordRepository
from crunch_node.entities.feed_record import FeedIngestionState, FeedRecord
from crunch_node.feeds.base import DataFeed
from crunch_node.feeds.contracts import FeedDataRecord, FeedFetchRequest


@dataclass(frozen=True)
class BackfillRequest:
    source: str
    subjects: tuple[str, ...]
    kind: str
    granularity: str
    start: datetime
    end: datetime
    page_size: int = 500
    cursor_ts: datetime | None = None  # Resume from this timestamp
    job_id: str | None = None  # Backfill job ID for progress tracking


@dataclass
class BackfillResult:
    records_written: int = 0
    pages_fetched: int = 0


class BackfillService:
    def __init__(
        self, feed: DataFeed, repository: FeedRecordRepository, job_repository=None
    ) -> None:
        self.feed = feed
        self.repository = repository
        self.job_repository = job_repository
        self.logger = logging.getLogger(__name__)

    async def run(self, request: BackfillRequest) -> BackfillResult:
        result = BackfillResult()

        # Resume from cursor if provided, otherwise start from beginning
        start_ts = int((request.cursor_ts or request.start).timestamp())
        end_ts = int(request.end.timestamp())

        # Mark job as running
        if self.job_repository and request.job_id:
            self.job_repository.set_status(request.job_id, "running")

        try:
            for subject in request.subjects:
                subject_cursor = start_ts
                while subject_cursor < end_ts:
                    req = FeedFetchRequest(
                        subjects=(subject,),
                        kind=request.kind,
                        granularity=request.granularity,
                        start_ts=subject_cursor,
                        end_ts=end_ts,
                        limit=request.page_size,
                    )

                    records = await self.feed.fetch(req)
                    result.pages_fetched += 1

                    if not records:
                        break

                    converted = [_feed_to_domain(request.source, r) for r in records]
                    written = self.repository.append_records(converted)
                    result.records_written += written

                    max_ts = max(r.ts_event for r in records)
                    if max_ts <= subject_cursor:
                        break
                    subject_cursor = max_ts + 1

                    # Update watermark (DB repo) or no-op (parquet sink)
                    self.repository.set_watermark(
                        FeedIngestionState(
                            source=request.source,
                            subject=subject,
                            kind=request.kind,
                            granularity=request.granularity,
                            last_event_ts=datetime.fromtimestamp(max_ts, tz=UTC),
                            meta={"phase": "backfill-manual"},
                        )
                    )

                    # Update job progress
                    if self.job_repository and request.job_id:
                        self.job_repository.update_progress(
                            request.job_id,
                            cursor_ts=datetime.fromtimestamp(subject_cursor, tz=UTC),
                            records_written=result.records_written,
                            pages_fetched=result.pages_fetched,
                        )

                    self.logger.info(
                        "backfill page subject=%s wrote=%d cursor=%s",
                        subject,
                        written,
                        datetime.fromtimestamp(subject_cursor, tz=UTC).isoformat(),
                    )

            # Mark job as completed
            if self.job_repository and request.job_id:
                self.job_repository.set_status(request.job_id, "completed")

        except Exception as exc:
            if self.job_repository and request.job_id:
                self.job_repository.set_status(request.job_id, "failed", error=str(exc))
            raise

        return result


def _feed_to_domain(source: str, record: FeedDataRecord) -> FeedRecord:
    return FeedRecord(
        source=source,
        subject=record.subject,
        kind=record.kind,
        granularity=record.granularity,
        ts_event=datetime.fromtimestamp(int(record.ts_event), tz=UTC),
        values=dict(record.values),
        meta=dict(record.metadata),
    )
