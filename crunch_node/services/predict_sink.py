"""Feed sink that triggers predictions on each record."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from crunch_node.feeds import FeedDataRecord

if TYPE_CHECKING:
    from crunch_node.services.feed_window import FeedWindow
    from crunch_node.services.realtime_predict import RealtimePredictService


class PredictSink:
    def __init__(
        self,
        predict_service: RealtimePredictService,
        feed_window: FeedWindow,
    ):
        self.predict_service = predict_service
        self.feed_window = feed_window
        self.logger = logging.getLogger(__name__)

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
            await self.predict_service.process_tick(
                raw_input=raw_input,
                feed_timing=feed_timing,
            )
        except Exception as exc:
            self.logger.exception("Prediction failed: %s", exc)

    def _build_input(self, subject: str) -> dict[str, Any]:
        return self.feed_window.get_input(subject)
