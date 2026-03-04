"""Tests for backfill and data-serving endpoints on the report worker."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from crunch_node.entities.feed_record import FeedRecord
from crunch_node.services.parquet_sink import ParquetBackfillSink


def _make_record(ts_event: datetime) -> FeedRecord:
    return FeedRecord(
        source="binance",
        subject="BTC",
        kind="candle",
        granularity="1m",
        ts_event=ts_event,
        values={
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 50.0,
        },
    )


class TestBackfillEndpoints(unittest.TestCase):
    """Test backfill management endpoints."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        # Patch the parquet sink and backfill data dir before importing app
        import crunch_node.workers.report_worker as rw

        self._original_sink = rw._parquet_sink
        self._original_dir = rw.BACKFILL_DATA_DIR
        rw._parquet_sink = ParquetBackfillSink(base_dir=self.tmp_dir)
        rw.BACKFILL_DATA_DIR = self.tmp_dir
        self.client = TestClient(rw.app)

    def tearDown(self):
        import crunch_node.workers.report_worker as rw

        rw._parquet_sink = self._original_sink
        rw.BACKFILL_DATA_DIR = self._original_dir

    def test_get_backfill_feeds(self):
        """GET /reports/backfill/feeds returns feed list."""
        from crunch_node.workers.report_worker import (
            app,
            get_feed_record_repository,
        )

        mock_repo = MagicMock()
        mock_repo.list_indexed_feeds.return_value = [
            {
                "source": "binance",
                "subject": "BTC",
                "kind": "candle",
                "granularity": "1m",
                "record_count": 100,
            }
        ]

        app.dependency_overrides[get_feed_record_repository] = lambda: mock_repo
        try:
            resp = self.client.get("/reports/backfill/feeds")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["source"], "binance")
        finally:
            app.dependency_overrides.clear()

    def test_post_backfill_creates_job(self):
        """POST /reports/backfill creates a job and returns 201."""
        from crunch_node.workers.report_worker import (
            app,
            get_backfill_job_repository,
            get_feed_record_repository,
        )

        mock_backfill_repo = MagicMock()
        mock_backfill_repo.get_running.return_value = None
        mock_job = MagicMock()
        mock_job.id = "job-1"
        mock_job.source = "binance"
        mock_job.subject = "BTC"
        mock_job.kind = "candle"
        mock_job.granularity = "1m"
        mock_job.start_ts = datetime(2026, 1, 1, tzinfo=UTC)
        mock_job.end_ts = datetime(2026, 2, 1, tzinfo=UTC)
        mock_job.cursor_ts = datetime(2026, 1, 1, tzinfo=UTC)
        mock_job.records_written = 0
        mock_job.pages_fetched = 0
        mock_job.status = "pending"
        mock_job.error = None
        mock_job.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        mock_job.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
        mock_backfill_repo.create.return_value = mock_job

        mock_feed_repo = MagicMock()

        app.dependency_overrides[get_backfill_job_repository] = lambda: (
            mock_backfill_repo
        )
        app.dependency_overrides[get_feed_record_repository] = lambda: mock_feed_repo
        try:
            resp = self.client.post(
                "/reports/backfill",
                json={
                    "source": "binance",
                    "subject": "BTC",
                    "kind": "candle",
                    "granularity": "1m",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-02-01T00:00:00Z",
                },
            )
            self.assertEqual(resp.status_code, 201)
            data = resp.json()
            self.assertEqual(data["id"], "job-1")
            self.assertEqual(data["status"], "pending")
        finally:
            app.dependency_overrides.clear()

    def test_post_backfill_returns_409_when_running(self):
        """POST /reports/backfill returns 409 if a job is already running."""
        from crunch_node.workers.report_worker import (
            app,
            get_backfill_job_repository,
            get_feed_record_repository,
        )

        mock_backfill_repo = MagicMock()
        running_job = MagicMock()
        running_job.id = "running-job"
        running_job.status = "running"
        mock_backfill_repo.get_running.return_value = running_job

        mock_feed_repo = MagicMock()

        app.dependency_overrides[get_backfill_job_repository] = lambda: (
            mock_backfill_repo
        )
        app.dependency_overrides[get_feed_record_repository] = lambda: mock_feed_repo
        try:
            resp = self.client.post(
                "/reports/backfill",
                json={
                    "source": "binance",
                    "subject": "BTC",
                    "kind": "candle",
                    "granularity": "1m",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-02-01T00:00:00Z",
                },
            )
            self.assertEqual(resp.status_code, 409)
        finally:
            app.dependency_overrides.clear()

    def test_list_backfill_jobs(self):
        """GET /reports/backfill/jobs returns list of jobs."""
        from crunch_node.workers.report_worker import (
            app,
            get_backfill_job_repository,
        )

        mock_job = MagicMock()
        mock_job.id = "job-1"
        mock_job.source = "binance"
        mock_job.subject = "BTC"
        mock_job.kind = "candle"
        mock_job.granularity = "1m"
        mock_job.start_ts = datetime(2026, 1, 1, tzinfo=UTC)
        mock_job.end_ts = datetime(2026, 2, 1, tzinfo=UTC)
        mock_job.cursor_ts = datetime(2026, 1, 15, tzinfo=UTC)
        mock_job.records_written = 500
        mock_job.pages_fetched = 10
        mock_job.status = "completed"
        mock_job.error = None
        mock_job.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        mock_job.updated_at = datetime(2026, 1, 15, tzinfo=UTC)

        mock_repo = MagicMock()
        mock_repo.find.return_value = [mock_job]

        app.dependency_overrides[get_backfill_job_repository] = lambda: mock_repo
        try:
            resp = self.client.get("/reports/backfill/jobs")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["status"], "completed")
            self.assertEqual(data[0]["records_written"], 500)
        finally:
            app.dependency_overrides.clear()

    def test_get_backfill_job_with_progress(self):
        """GET /reports/backfill/jobs/{id} returns job with progress_pct."""
        from crunch_node.workers.report_worker import (
            app,
            get_backfill_job_repository,
        )

        mock_job = MagicMock()
        mock_job.id = "job-1"
        mock_job.source = "binance"
        mock_job.subject = "BTC"
        mock_job.kind = "candle"
        mock_job.granularity = "1m"
        mock_job.start_ts = datetime(2026, 1, 1, tzinfo=UTC)
        mock_job.end_ts = datetime(2026, 2, 1, tzinfo=UTC)
        mock_job.cursor_ts = datetime(2026, 1, 16, tzinfo=UTC)  # ~50%
        mock_job.records_written = 250
        mock_job.pages_fetched = 5
        mock_job.status = "running"
        mock_job.error = None
        mock_job.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        mock_job.updated_at = datetime(2026, 1, 16, tzinfo=UTC)

        mock_repo = MagicMock()
        mock_repo.get.return_value = mock_job

        app.dependency_overrides[get_backfill_job_repository] = lambda: mock_repo
        try:
            resp = self.client.get("/reports/backfill/jobs/job-1")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "running")
            self.assertGreater(data["progress_pct"], 40.0)
            self.assertLess(data["progress_pct"], 60.0)
        finally:
            app.dependency_overrides.clear()

    def test_get_backfill_job_not_found(self):
        """GET /reports/backfill/jobs/{id} returns 404 for unknown job."""
        from crunch_node.workers.report_worker import (
            app,
            get_backfill_job_repository,
        )

        mock_repo = MagicMock()
        mock_repo.get.return_value = None

        app.dependency_overrides[get_backfill_job_repository] = lambda: mock_repo
        try:
            resp = self.client.get("/reports/backfill/jobs/nonexistent")
            self.assertEqual(resp.status_code, 404)
        finally:
            app.dependency_overrides.clear()


class TestDataServingEndpoints(unittest.TestCase):
    """Test parquet data serving endpoints."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        import crunch_node.workers.report_worker as rw

        self._original_sink = rw._parquet_sink
        rw._parquet_sink = ParquetBackfillSink(base_dir=self.tmp_dir)
        self.sink = rw._parquet_sink
        self.client = TestClient(rw.app)

    def tearDown(self):
        import crunch_node.workers.report_worker as rw

        rw._parquet_sink = self._original_sink

    def test_index_empty(self):
        """GET /data/backfill/index returns empty list when no data."""
        resp = self.client.get("/data/backfill/index")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_index_with_data(self):
        """GET /data/backfill/index returns manifest after writing data."""
        self.sink.append_records(
            [_make_record(datetime(2026, 1, 15, 10, 0, tzinfo=UTC))]
        )

        resp = self.client.get("/data/backfill/index")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["date"], "2026-01-15")
        self.assertEqual(data[0]["records"], 1)
        self.assertIn("size_bytes", data[0])

    def test_serve_parquet_file(self):
        """GET /data/backfill/{path} serves the parquet file."""
        self.sink.append_records(
            [_make_record(datetime(2026, 1, 15, 10, 0, tzinfo=UTC))]
        )

        resp = self.client.get(
            "/data/backfill/binance/BTC/candle/1m/2026-01-15.parquet"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/octet-stream", resp.headers.get("content-type", ""))
        # Should be valid parquet content
        self.assertGreater(len(resp.content), 0)

    def test_serve_missing_file_returns_404(self):
        """GET /data/backfill/{path} returns 404 for missing files."""
        resp = self.client.get(
            "/data/backfill/binance/BTC/candle/1m/2026-99-99.parquet"
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
