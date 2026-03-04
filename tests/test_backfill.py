"""Tests for the backfill script and service."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from crunch_node.feeds.contracts import FeedDataRecord, FeedFetchRequest


class TestBackfillService(unittest.TestCase):
    """Unit tests for the backfill runner logic."""

    def _make_records(
        self, subject: str, base_ts: int, count: int
    ) -> list[FeedDataRecord]:
        return [
            FeedDataRecord(
                source="binance",
                subject=subject,
                kind="candle",
                granularity="1m",
                ts_event=base_ts + i * 60,
                values={
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 10,
                },
            )
            for i in range(count)
        ]

    def test_backfill_paginates_through_time_range(self):
        from crunch_node.services.backfill import BackfillRequest, BackfillService

        repo = MagicMock()
        repo.append_records = MagicMock(return_value=5)
        repo.set_watermark = MagicMock()

        feed = AsyncMock()
        feed.fetch = AsyncMock(
            side_effect=[self._make_records("BTC", 1707700000, 5), []]
        )

        request = BackfillRequest(
            source="binance",
            subjects=("BTC",),
            kind="candle",
            granularity="1m",
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 2, 1, 0, 5, tzinfo=UTC),
            page_size=500,
        )

        service = BackfillService(feed=feed, repository=repo)
        result = asyncio.run(service.run(request))

        self.assertEqual(result.records_written, 5)
        self.assertGreaterEqual(feed.fetch.call_count, 1)
        repo.append_records.assert_called()

    def test_backfill_returns_zero_when_no_data(self):
        from crunch_node.services.backfill import BackfillRequest, BackfillService

        repo = MagicMock()
        repo.append_records = MagicMock(return_value=0)

        feed = AsyncMock()
        feed.fetch = AsyncMock(return_value=[])

        request = BackfillRequest(
            source="binance",
            subjects=("BTC",),
            kind="candle",
            granularity="1m",
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 2, 1, 0, 5, tzinfo=UTC),
        )

        service = BackfillService(feed=feed, repository=repo)
        result = asyncio.run(service.run(request))

        self.assertEqual(result.records_written, 0)

    def test_backfill_advances_start_past_last_record(self):
        from crunch_node.services.backfill import BackfillRequest, BackfillService

        repo = MagicMock()
        repo.append_records = MagicMock(return_value=3)
        repo.set_watermark = MagicMock()

        feed = AsyncMock()
        feed.fetch = AsyncMock(side_effect=[self._make_records("BTC", 1000, 3), []])

        request = BackfillRequest(
            source="binance",
            subjects=("BTC",),
            kind="candle",
            granularity="1m",
            start=datetime(1970, 1, 1, 0, 16, 40, tzinfo=UTC),
            end=datetime(1970, 1, 1, 0, 25, tzinfo=UTC),
        )

        service = BackfillService(feed=feed, repository=repo)
        asyncio.run(service.run(request))

        second_call = feed.fetch.call_args_list[1]
        req: FeedFetchRequest = second_call[0][0]
        self.assertGreater(req.start_ts, 1000)

    def test_backfill_with_job_tracking(self):
        """BackfillService updates job status and progress when job_repository is provided."""
        from crunch_node.services.backfill import BackfillRequest, BackfillService

        repo = MagicMock()
        repo.append_records = MagicMock(return_value=5)
        repo.set_watermark = MagicMock()

        job_repo = MagicMock()

        # Records must be in the requested time range for cursor to advance
        start_ts = int(datetime(2026, 2, 1, tzinfo=UTC).timestamp())
        feed = AsyncMock()
        feed.fetch = AsyncMock(side_effect=[self._make_records("BTC", start_ts, 5), []])

        request = BackfillRequest(
            source="binance",
            subjects=("BTC",),
            kind="candle",
            granularity="1m",
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 2, 1, 0, 10, tzinfo=UTC),
            job_id="test-job-123",
        )

        service = BackfillService(feed=feed, repository=repo, job_repository=job_repo)
        result = asyncio.run(service.run(request))

        self.assertEqual(result.records_written, 5)

        # Should have set status to running, then completed
        job_repo.set_status.assert_any_call("test-job-123", "running")
        job_repo.set_status.assert_any_call("test-job-123", "completed")

        # Should have updated progress at least once
        job_repo.update_progress.assert_called()

    def test_backfill_job_marked_failed_on_error(self):
        """BackfillService marks job as failed when an exception occurs."""
        from crunch_node.services.backfill import BackfillRequest, BackfillService

        repo = MagicMock()
        repo.append_records = MagicMock(side_effect=RuntimeError("DB exploded"))
        repo.set_watermark = MagicMock()

        job_repo = MagicMock()

        feed = AsyncMock()
        feed.fetch = AsyncMock(return_value=self._make_records("BTC", 1707700000, 5))

        request = BackfillRequest(
            source="binance",
            subjects=("BTC",),
            kind="candle",
            granularity="1m",
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 2, 1, 0, 5, tzinfo=UTC),
            job_id="test-job-456",
        )

        service = BackfillService(feed=feed, repository=repo, job_repository=job_repo)
        with self.assertRaises(RuntimeError):
            asyncio.run(service.run(request))

        job_repo.set_status.assert_any_call(
            "test-job-456", "failed", error="DB exploded"
        )

    def test_backfill_resumes_from_cursor(self):
        """BackfillService starts from cursor_ts when provided."""
        from crunch_node.services.backfill import BackfillRequest, BackfillService

        repo = MagicMock()
        repo.append_records = MagicMock(return_value=0)
        repo.set_watermark = MagicMock()

        feed = AsyncMock()
        feed.fetch = AsyncMock(return_value=[])

        # cursor_ts is halfway through the range
        cursor = datetime(2026, 2, 1, 0, 3, tzinfo=UTC)
        request = BackfillRequest(
            source="binance",
            subjects=("BTC",),
            kind="candle",
            granularity="1m",
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 2, 1, 0, 5, tzinfo=UTC),
            cursor_ts=cursor,
        )

        service = BackfillService(feed=feed, repository=repo)
        asyncio.run(service.run(request))

        # The fetch should start from cursor_ts, not start
        call_args = feed.fetch.call_args_list[0]
        req: FeedFetchRequest = call_args[0][0]
        self.assertEqual(req.start_ts, int(cursor.timestamp()))

    def test_backfill_without_job_tracking(self):
        """BackfillService works normally without job_repository (backward compat)."""
        from crunch_node.services.backfill import BackfillRequest, BackfillService

        repo = MagicMock()
        repo.append_records = MagicMock(return_value=3)
        repo.set_watermark = MagicMock()

        feed = AsyncMock()
        feed.fetch = AsyncMock(
            side_effect=[self._make_records("BTC", 1707700000, 3), []]
        )

        request = BackfillRequest(
            source="binance",
            subjects=("BTC",),
            kind="candle",
            granularity="1m",
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 2, 1, 0, 5, tzinfo=UTC),
        )

        service = BackfillService(feed=feed, repository=repo)
        result = asyncio.run(service.run(request))
        self.assertEqual(result.records_written, 3)


if __name__ == "__main__":
    unittest.main()
