"""Feed sink that triggers predictions on each record."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from crunch_node.entities.feed_record import FeedIngestionState, FeedRecord
from crunch_node.feeds import FeedDataRecord

if TYPE_CHECKING:
    from crunch_node.db.feed_records import DBFeedRecordRepository
    from crunch_node.services.feed_window import FeedWindow
    from crunch_node.services.realtime_predict import RealtimePredictService


class PredictSink:
    """FeedSink implementation that triggers predictions on each feed record.

    Hot path: update window → build input → predict
    Cold path: async persist feed record + update watermark
    """

    def __init__(
        self,
        predict_service: RealtimePredictService,
        feed_repository: DBFeedRecordRepository,
        feed_window: FeedWindow,
        source: str,
    ):
        self.predict_service = predict_service
        self.feed_repository = feed_repository
        self.feed_window = feed_window
        self.source = source
        self.logger = logging.getLogger(__name__)
        self._persist_tasks: set[asyncio.Task] = set()

    async def on_record(self, record: FeedDataRecord) -> None:
        feed_received_us = time.time_ns() // 1000

        self.feed_window.append(record)
        feed_normalized_us = time.time_ns() // 1000

        raw_input = self._build_input(record.subject)

        feed_timing = {
            "feed_received_us": feed_received_us,
            "feed_normalized_us": feed_normalized_us,
            "feed_persisted_us": feed_normalized_us,
        }

        try:
            await self.predict_service.run_once(
                raw_input=raw_input,
                feed_timing=feed_timing,
            )
        except Exception as exc:
            self.logger.exception("Prediction failed: %s", exc)

        task = asyncio.create_task(
            self._persist_async(record, feed_received_us, feed_normalized_us)
        )
        self._persist_tasks.add(task)
        task.add_done_callback(self._persist_tasks.discard)

    def _build_input(self, subject: str) -> dict[str, Any]:
        return self.feed_window.get_input(subject)

    async def _persist_async(
        self,
        record: FeedDataRecord,
        feed_received_us: int,
        feed_normalized_us: int,
    ) -> None:
        try:
            domain = self._to_domain_record(record, feed_received_us, feed_normalized_us)
            self.feed_repository.append_records([domain])
            self.feed_repository.set_watermark(
                FeedIngestionState(
                    source=record.source or self.source,
                    subject=record.subject,
                    kind=record.kind,
                    granularity=record.granularity,
                    last_event_ts=datetime.fromtimestamp(record.ts_event, tz=UTC),
                    meta={"phase": "listen"},
                )
            )
        except Exception as exc:
            self.logger.warning("Async persist failed: %s", exc)

    def _to_domain_record(
        self,
        record: FeedDataRecord,
        feed_received_us: int,
        feed_normalized_us: int,
    ) -> FeedRecord:
        source = record.source or self.source
        meta = dict(record.metadata)
        meta.setdefault("timing", {})["feed_received_us"] = feed_received_us
        meta["timing"]["feed_normalized_us"] = feed_normalized_us

        return FeedRecord(
            source=source,
            subject=record.subject,
            kind=record.kind,
            granularity=record.granularity,
            ts_event=datetime.fromtimestamp(int(record.ts_event), tz=UTC),
            values=dict(record.values),
            meta=meta,
        )
