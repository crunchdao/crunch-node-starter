"""
Unit tests for TimingCollector functionality.
"""

import threading
import time
from unittest.mock import patch

import pytest

from crunch_node.metrics.timing import TimingCollector, timing_collector


class TestTimingCollector:
    """Test TimingCollector functionality."""

    def setup_method(self):
        """Reset timing collector for each test."""
        # Clear the buffer and reset to default config
        timing_collector.configure(enabled=False, buffer_size=10000)
        timing_collector.clear()

    def test_singleton_behavior(self):
        """Test that TimingCollector is a singleton."""
        instance1 = TimingCollector()
        instance2 = TimingCollector()
        assert instance1 is instance2
        assert instance1 is timing_collector

    def test_configure(self):
        """Test configuration of the timing collector."""
        timing_collector.configure(enabled=True, buffer_size=5000)
        assert timing_collector.enabled is True
        assert timing_collector._buffer_size == 5000

    def test_disabled_by_default(self):
        """Test that timing collection is disabled by default."""
        assert timing_collector.enabled is False

    def test_record_timing_when_disabled(self):
        """Test that no records are collected when disabled."""
        timing_collector.configure(enabled=False)
        timing_collector.record_timing("test-id", {"test_time": 12345})
        assert timing_collector.buffer_size == 0

    def test_record_timing_when_enabled(self):
        """Test timing record collection when enabled."""
        timing_collector.configure(enabled=True)

        timing_data = {"feed_received_us": 1000, "feed_persisted_us": 2000}
        timing_collector.record_timing("test-id-1", timing_data)

        assert timing_collector.buffer_size == 1
        records = timing_collector.get_all_records()
        assert len(records) == 1
        assert records[0]["prediction_id"] == "test-id-1"
        assert records[0]["feed_received_us"] == 1000
        assert records[0]["feed_persisted_us"] == 2000
        assert "timestamp" in records[0]

    def test_buffer_size_limit(self):
        """Test that buffer respects size limit."""
        timing_collector.configure(enabled=True, buffer_size=3)

        # Add 5 records, should only keep the last 3
        for i in range(5):
            timing_collector.record_timing(f"test-id-{i}", {"time": i})

        assert timing_collector.buffer_size == 3
        records = timing_collector.get_all_records()
        ids = [r["prediction_id"] for r in records]
        assert ids == ["test-id-2", "test-id-3", "test-id-4"]

    def test_get_recent(self):
        """Test getting recent records."""
        timing_collector.configure(enabled=True)

        # Add 10 records
        for i in range(10):
            timing_collector.record_timing(f"test-id-{i}", {"time": i})

        recent = timing_collector.get_recent(3)
        assert len(recent) == 3
        ids = [r["prediction_id"] for r in recent]
        assert ids == ["test-id-7", "test-id-8", "test-id-9"]

    def test_get_recent_more_than_available(self):
        """Test getting recent when requesting more than available."""
        timing_collector.configure(enabled=True)

        timing_collector.record_timing("test-id-1", {"time": 1})

        recent = timing_collector.get_recent(10)
        assert len(recent) == 1
        assert recent[0]["prediction_id"] == "test-id-1"

    def test_clear(self):
        """Test clearing the buffer."""
        timing_collector.configure(enabled=True)
        timing_collector.record_timing("test-id", {"time": 1})
        assert timing_collector.buffer_size == 1

        timing_collector.clear()
        assert timing_collector.buffer_size == 0
        assert timing_collector.get_all_records() == []

    def test_stage_latency_calculation(self):
        """Test calculation of stage latencies."""
        timing_collector.configure(enabled=True)

        # Add records with complete timing data
        timing_collector.record_timing(
            "test-1",
            {
                "feed_received_us": 1000,
                "feed_normalized_us": 1500,
                "feed_persisted_us": 2000,
                "models_dispatched_us": 3000,
                "models_completed_us": 4000,
                "persistence_completed_us": 5000,
            },
        )

        timing_collector.record_timing(
            "test-2",
            {
                "feed_received_us": 1100,
                "feed_normalized_us": 1600,
                "feed_persisted_us": 2200,
                "models_dispatched_us": 3300,
                "models_completed_us": 4400,
                "persistence_completed_us": 5500,
            },
        )

        metrics = timing_collector.get_metrics()

        assert "stage_latencies" in metrics
        stage_latencies = metrics["stage_latencies"]

        def get_stage(name):
            return next((s for s in stage_latencies if s["name"] == name), None)

        # Check feed ingestion stage (received to normalized)
        feed_ingestion = get_stage("feed_ingestion")
        assert feed_ingestion is not None
        assert feed_ingestion["count"] == 2
        assert feed_ingestion["mean_us"] == 500.0  # (500 + 500) / 2

        # Check model execution stage (dispatched to completed)
        model_execution = get_stage("model_execution")
        assert model_execution is not None
        assert model_execution["count"] == 2
        assert model_execution["mean_us"] == 1050.0  # (1000 + 1100) / 2

        # Check end-to-end (feed_received to persistence_completed)
        end_to_end = get_stage("end_to_end")
        assert end_to_end is not None
        assert end_to_end["count"] == 2
        # Test 1: 5000 - 1000 = 4000
        # Test 2: 5500 - 1100 = 4400
        # Mean: (4000 + 4400) / 2 = 4200
        assert end_to_end["mean_us"] == 4200.0

    def test_metrics_with_empty_buffer(self):
        """Test metrics when buffer is empty."""
        timing_collector.configure(enabled=True)

        metrics = timing_collector.get_metrics()

        assert metrics["enabled"] is True
        assert metrics["buffer_size"] == 0
        assert metrics["total_records"] == 0
        assert metrics["stage_latencies"] == []
        assert metrics["recent_samples"] == []

    def test_metrics_with_partial_timing_data(self):
        """Test metrics calculation with incomplete timing data."""
        timing_collector.configure(enabled=True)

        # Record with only some timing fields
        timing_collector.record_timing(
            "test-1",
            {
                "feed_received_us": 1000,
                "feed_normalized_us": 1500,
                "feed_persisted_us": 2000,
                # Missing model timing
                "persistence_completed_us": 5000,
            },
        )

        metrics = timing_collector.get_metrics()
        stage_latencies = metrics["stage_latencies"]

        def get_stage(name):
            return next((s for s in stage_latencies if s["name"] == name), None)

        # Feed ingestion should work
        feed_ingestion = get_stage("feed_ingestion")
        assert feed_ingestion["count"] == 1

        # Model execution should have no data
        model_execution = get_stage("model_execution")
        assert model_execution["count"] == 0
        assert model_execution["mean_us"] is None

    def test_thread_safety(self):
        """Test thread safety of timing collection."""
        timing_collector.configure(enabled=True, buffer_size=1000)
        timing_collector.clear()

        def add_records(thread_id):
            for i in range(100):
                timing_collector.record_timing(f"thread-{thread_id}-{i}", {"time": i})

        # Start multiple threads
        threads = []
        for thread_id in range(5):
            thread = threading.Thread(target=add_records, args=(thread_id,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Should have 500 records total
        assert timing_collector.buffer_size == 500

        # All records should be present
        records = timing_collector.get_all_records()
        assert len(records) == 500

    def test_percentile_calculation(self):
        """Test percentile calculation in metrics."""
        timing_collector.configure(enabled=True)

        # Add records with known latencies for easy testing
        for i in range(100):
            timing_collector.record_timing(
                f"test-{i}",
                {
                    "feed_received_us": 1000,
                    "feed_normalized_us": 1000 + i,  # Latencies from 0 to 99
                },
            )

        metrics = timing_collector.get_metrics()
        stage_latencies = metrics["stage_latencies"]
        feed_ingestion = next(
            (s for s in stage_latencies if s["name"] == "feed_ingestion"), None
        )

        assert feed_ingestion["count"] == 100
        assert feed_ingestion["min_us"] == 0.0
        assert feed_ingestion["max_us"] == 99.0
        assert feed_ingestion["median_us"] == 49.5  # Middle of 0-99
        assert feed_ingestion["p95_us"] >= 90  # Should be around 95th percentile
        assert feed_ingestion["p99_us"] >= 95  # Should be around 99th percentile

    def test_outlier_filtering(self):
        """Test that records with e2e latency above threshold are filtered."""
        timing_collector.configure(
            enabled=True, outlier_threshold_us=100_000
        )  # 100ms threshold

        # Add normal record (10ms e2e)
        timing_collector.record_timing(
            "normal",
            {
                "feed_received_us": 1000,
                "feed_normalized_us": 1500,
                "persistence_completed_us": 11000,  # 10ms total
            },
        )

        # Add outlier record (5 seconds e2e - simulates runner init)
        timing_collector.record_timing(
            "outlier",
            {
                "feed_received_us": 2000,
                "feed_normalized_us": 2500,
                "persistence_completed_us": 5_002_000,  # 5 seconds total
            },
        )

        metrics = timing_collector.get_metrics()

        assert metrics["total_records"] == 2
        assert metrics["filtered_records"] == 1
        assert metrics["outliers_removed"] == 1

        # e2e stats should only include the normal record
        e2e = next(
            (s for s in metrics["stage_latencies"] if s["name"] == "end_to_end"), None
        )
        assert e2e["count"] == 1
        assert e2e["mean_us"] == 10000  # 10ms from the normal record

    def test_outlier_threshold_configuration(self):
        """Test that outlier threshold can be configured."""
        timing_collector.configure(
            enabled=True, outlier_threshold_us=50_000
        )  # 50ms threshold

        # Record with 60ms latency should be filtered
        timing_collector.record_timing(
            "slow",
            {
                "feed_received_us": 1000,
                "persistence_completed_us": 61000,  # 60ms
            },
        )

        # Record with 40ms latency should not be filtered
        timing_collector.record_timing(
            "fast",
            {
                "feed_received_us": 2000,
                "persistence_completed_us": 42000,  # 40ms
            },
        )

        metrics = timing_collector.get_metrics()
        assert metrics["outliers_removed"] == 1
        assert metrics["filtered_records"] == 1
