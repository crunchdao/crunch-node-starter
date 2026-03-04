"""Tests for DBBackfillJobRepository."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from sqlmodel import Session, create_engine

from crunch_node.db.backfill_jobs import BackfillJobStatus, DBBackfillJobRepository
from crunch_node.db.tables.backfill import BackfillJobRow


def _make_engine():
    engine = create_engine("sqlite:///:memory:")
    # Only create the backfill_jobs table (others use JSONB which SQLite can't handle)
    BackfillJobRow.metadata.create_all(engine, tables=[BackfillJobRow.__table__])
    return engine


class TestDBBackfillJobRepository(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        self.session = Session(self.engine)
        self.repo = DBBackfillJobRepository(self.session)

    def tearDown(self):
        self.session.close()

    def _create_job(self, **overrides):
        defaults = dict(
            source="binance",
            subject="BTC",
            kind="candle",
            granularity="1m",
            start_ts=datetime(2026, 1, 1, tzinfo=UTC),
            end_ts=datetime(2026, 2, 1, tzinfo=UTC),
        )
        defaults.update(overrides)
        return self.repo.create(**defaults)

    def test_create_returns_row_with_pending_status(self):
        job = self._create_job()
        self.assertEqual(job.status, BackfillJobStatus.PENDING)
        self.assertEqual(job.source, "binance")
        self.assertEqual(job.subject, "BTC")
        self.assertEqual(job.records_written, 0)
        self.assertEqual(job.pages_fetched, 0)
        self.assertIsNotNone(job.id)

    def test_create_sets_cursor_to_start(self):
        job = self._create_job()
        # SQLite strips tzinfo; compare naive
        expected = datetime(2026, 1, 1)
        actual = (
            job.cursor_ts.replace(tzinfo=None)
            if job.cursor_ts.tzinfo
            else job.cursor_ts
        )
        self.assertEqual(actual, expected)

    def test_get_returns_job(self):
        job = self._create_job()
        fetched = self.repo.get(job.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, job.id)

    def test_get_returns_none_for_unknown_id(self):
        self.assertIsNone(self.repo.get("nonexistent"))

    def test_find_returns_all_jobs(self):
        self._create_job(subject="BTC")
        self._create_job(subject="ETH")
        jobs = self.repo.find()
        self.assertEqual(len(jobs), 2)

    def test_find_filters_by_status(self):
        job = self._create_job()
        self.repo.set_status(job.id, BackfillJobStatus.COMPLETED)
        self._create_job()  # pending

        completed = self.repo.find(status=BackfillJobStatus.COMPLETED)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].id, job.id)

        pending = self.repo.find(status=BackfillJobStatus.PENDING)
        self.assertEqual(len(pending), 1)

    def test_get_running_returns_pending_job(self):
        self._create_job()
        running = self.repo.get_running()
        self.assertIsNotNone(running)

    def test_get_running_returns_running_job(self):
        job = self._create_job()
        self.repo.set_status(job.id, BackfillJobStatus.RUNNING)
        running = self.repo.get_running()
        self.assertIsNotNone(running)
        self.assertEqual(running.id, job.id)

    def test_get_running_returns_none_when_all_done(self):
        job = self._create_job()
        self.repo.set_status(job.id, BackfillJobStatus.COMPLETED)
        self.assertIsNone(self.repo.get_running())

    def test_update_progress(self):
        job = self._create_job()
        cursor = datetime(2026, 1, 15, tzinfo=UTC)
        self.repo.update_progress(
            job.id, cursor_ts=cursor, records_written=500, pages_fetched=10
        )

        updated = self.repo.get(job.id)
        # SQLite strips tzinfo; compare naive
        actual_cursor = (
            updated.cursor_ts.replace(tzinfo=None)
            if updated.cursor_ts.tzinfo
            else updated.cursor_ts
        )
        self.assertEqual(actual_cursor, datetime(2026, 1, 15))
        self.assertEqual(updated.records_written, 500)
        self.assertEqual(updated.pages_fetched, 10)

    def test_update_progress_noop_for_unknown_id(self):
        # Should not raise
        self.repo.update_progress(
            "nonexistent",
            cursor_ts=datetime(2026, 1, 1, tzinfo=UTC),
            records_written=0,
            pages_fetched=0,
        )

    def test_set_status(self):
        job = self._create_job()
        self.repo.set_status(job.id, BackfillJobStatus.RUNNING)
        self.assertEqual(self.repo.get(job.id).status, BackfillJobStatus.RUNNING)

    def test_set_status_with_error(self):
        job = self._create_job()
        self.repo.set_status(
            job.id, BackfillJobStatus.FAILED, error="Connection timeout"
        )
        updated = self.repo.get(job.id)
        self.assertEqual(updated.status, BackfillJobStatus.FAILED)
        self.assertEqual(updated.error, "Connection timeout")

    def test_set_status_noop_for_unknown_id(self):
        # Should not raise
        self.repo.set_status("nonexistent", BackfillJobStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
