"""Tests for ParquetBackfillSink."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq

from crunch_node.entities.feed_record import FeedRecord
from crunch_node.services.parquet_sink import ParquetBackfillSink


def _make_record(
    ts_event: datetime,
    source: str = "binance",
    subject: str = "BTC",
    kind: str = "candle",
    granularity: str = "1m",
    values: dict | None = None,
    meta: dict | None = None,
) -> FeedRecord:
    return FeedRecord(
        source=source,
        subject=subject,
        kind=kind,
        granularity=granularity,
        ts_event=ts_event,
        values=values
        or {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 50.0},
        meta=meta or {},
    )


class TestParquetBackfillSink(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.sink = ParquetBackfillSink(base_dir=self.tmp_dir)

    def test_write_creates_partitioned_parquet_file(self):
        records = [
            _make_record(datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)),
            _make_record(datetime(2026, 1, 15, 10, 1, 0, tzinfo=UTC)),
        ]
        count = self.sink.append_records(records)
        self.assertEqual(count, 2)

        expected_path = (
            Path(self.tmp_dir)
            / "binance"
            / "BTC"
            / "candle"
            / "1m"
            / "2026-01-15.parquet"
        )
        self.assertTrue(expected_path.exists())

    def test_read_back_values(self):
        records = [
            _make_record(
                datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC),
                values={
                    "open": 100.0,
                    "high": 105.0,
                    "low": 98.0,
                    "close": 103.0,
                    "volume": 200.0,
                },
            ),
        ]
        self.sink.append_records(records)

        path = (
            Path(self.tmp_dir)
            / "binance"
            / "BTC"
            / "candle"
            / "1m"
            / "2026-01-15.parquet"
        )
        table = pq.read_table(path)
        self.assertEqual(table.num_rows, 1)
        self.assertAlmostEqual(table.column("open")[0].as_py(), 100.0)
        self.assertAlmostEqual(table.column("high")[0].as_py(), 105.0)
        self.assertAlmostEqual(table.column("close")[0].as_py(), 103.0)
        self.assertAlmostEqual(table.column("volume")[0].as_py(), 200.0)

    def test_deduplication_on_overlap(self):
        ts = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
        records1 = [
            _make_record(
                ts,
                values={
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 10.0,
                },
            )
        ]
        records2 = [
            _make_record(
                ts,
                values={
                    "open": 200.0,
                    "high": 201.0,
                    "low": 199.0,
                    "close": 200.0,
                    "volume": 20.0,
                },
            )
        ]

        self.sink.append_records(records1)
        self.sink.append_records(records2)

        path = (
            Path(self.tmp_dir)
            / "binance"
            / "BTC"
            / "candle"
            / "1m"
            / "2026-01-15.parquet"
        )
        table = pq.read_table(path)
        # Should have 1 row (deduplicated), with the second write winning
        self.assertEqual(table.num_rows, 1)
        self.assertAlmostEqual(table.column("open")[0].as_py(), 200.0)

    def test_multiple_dates_create_separate_files(self):
        records = [
            _make_record(datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)),
            _make_record(datetime(2026, 1, 16, 10, 0, 0, tzinfo=UTC)),
        ]
        self.sink.append_records(records)

        day1 = (
            Path(self.tmp_dir)
            / "binance"
            / "BTC"
            / "candle"
            / "1m"
            / "2026-01-15.parquet"
        )
        day2 = (
            Path(self.tmp_dir)
            / "binance"
            / "BTC"
            / "candle"
            / "1m"
            / "2026-01-16.parquet"
        )
        self.assertTrue(day1.exists())
        self.assertTrue(day2.exists())

    def test_records_sorted_by_ts_event(self):
        records = [
            _make_record(datetime(2026, 1, 15, 10, 5, 0, tzinfo=UTC)),
            _make_record(datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)),
            _make_record(datetime(2026, 1, 15, 10, 2, 0, tzinfo=UTC)),
        ]
        self.sink.append_records(records)

        path = (
            Path(self.tmp_dir)
            / "binance"
            / "BTC"
            / "candle"
            / "1m"
            / "2026-01-15.parquet"
        )
        table = pq.read_table(path)
        timestamps = [row.as_py() for row in table.column("ts_event")]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_non_standard_values_go_to_meta(self):
        records = [
            _make_record(
                datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC),
                values={
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 10.0,
                    "vwap": 100.3,
                },
            ),
        ]
        self.sink.append_records(records)

        path = (
            Path(self.tmp_dir)
            / "binance"
            / "BTC"
            / "candle"
            / "1m"
            / "2026-01-15.parquet"
        )
        table = pq.read_table(path)
        meta_str = table.column("meta")[0].as_py()
        meta = json.loads(meta_str)
        self.assertAlmostEqual(meta["vwap"], 100.3)

    def test_list_files_returns_manifest(self):
        records = [
            _make_record(datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)),
            _make_record(datetime(2026, 1, 16, 10, 0, 0, tzinfo=UTC)),
        ]
        self.sink.append_records(records)

        manifest = self.sink.list_files()
        self.assertEqual(len(manifest), 2)
        dates = {entry["date"] for entry in manifest}
        self.assertEqual(dates, {"2026-01-15", "2026-01-16"})
        for entry in manifest:
            self.assertIn("path", entry)
            self.assertIn("records", entry)
            self.assertIn("size_bytes", entry)
            self.assertGreater(entry["records"], 0)
            self.assertGreater(entry["size_bytes"], 0)

    def test_list_files_empty_dir(self):
        manifest = self.sink.list_files()
        self.assertEqual(manifest, [])

    def test_read_file_returns_path(self):
        records = [_make_record(datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC))]
        self.sink.append_records(records)

        path = self.sink.read_file("binance/BTC/candle/1m/2026-01-15.parquet")
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())

    def test_read_file_returns_none_for_missing(self):
        self.assertIsNone(self.sink.read_file("nonexistent/file.parquet"))

    def test_different_subjects_create_separate_dirs(self):
        records = [
            _make_record(datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC), subject="BTC"),
            _make_record(datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC), subject="ETH"),
        ]
        self.sink.append_records(records)

        btc = (
            Path(self.tmp_dir)
            / "binance"
            / "BTC"
            / "candle"
            / "1m"
            / "2026-01-15.parquet"
        )
        eth = (
            Path(self.tmp_dir)
            / "binance"
            / "ETH"
            / "candle"
            / "1m"
            / "2026-01-15.parquet"
        )
        self.assertTrue(btc.exists())
        self.assertTrue(eth.exists())

    def test_merge_appends_new_records(self):
        self.sink.append_records(
            [
                _make_record(datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)),
            ]
        )
        self.sink.append_records(
            [
                _make_record(datetime(2026, 1, 15, 10, 1, 0, tzinfo=UTC)),
            ]
        )

        path = (
            Path(self.tmp_dir)
            / "binance"
            / "BTC"
            / "candle"
            / "1m"
            / "2026-01-15.parquet"
        )
        table = pq.read_table(path)
        self.assertEqual(table.num_rows, 2)


if __name__ == "__main__":
    unittest.main()
