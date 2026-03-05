"""
Unit tests for TimingCollector functionality.
"""

import threading

import pytest

from crunch_node.metrics.timing import TimingCollector, timing_collector


class TestTimingCollector:
    """Test TimingCollector functionality."""

    def setup_method(self):
        """Reset timing collector for each test."""
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

        for i in range(5):
            timing_collector.record_timing(f"test-id-{i}", {"time": i})

        assert timing_collector.buffer_size == 3
        records = timing_collector.get_all_records()
        ids = [r["prediction_id"] for r in records]
        assert ids == ["test-id-2", "test-id-3", "test-id-4"]

    def test_get_recent(self):
        """Test getting recent records."""
        timing_collector.configure(enabled=True)

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

        feed_ingestion = get_stage("feed_ingestion")
        assert feed_ingestion is not None
        assert feed_ingestion["count"] == 2
        assert feed_ingestion["mean_us"] == 500.0

        model_execution = get_stage("model_execution")
        assert model_execution is not None
        assert model_execution["count"] == 2
        assert model_execution["mean_us"] == 1050.0

        end_to_end = get_stage("end_to_end")
        assert end_to_end is not None
        assert end_to_end["count"] == 2
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

        timing_collector.record_timing(
            "test-1",
            {
                "feed_received_us": 1000,
                "feed_normalized_us": 1500,
                "feed_persisted_us": 2000,
                "persistence_completed_us": 5000,
            },
        )

        metrics = timing_collector.get_metrics()
        stage_latencies = metrics["stage_latencies"]

        def get_stage(name):
            return next((s for s in stage_latencies if s["name"] == name), None)

        feed_ingestion = get_stage("feed_ingestion")
        assert feed_ingestion["count"] == 1

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

        threads = []
        for thread_id in range(5):
            thread = threading.Thread(target=add_records, args=(thread_id,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert timing_collector.buffer_size == 500

        records = timing_collector.get_all_records()
        assert len(records) == 500

    def test_percentile_calculation(self):
        """Test percentile calculation in metrics."""
        timing_collector.configure(enabled=True)

        for i in range(100):
            timing_collector.record_timing(
                f"test-{i}",
                {
                    "feed_received_us": 1000,
                    "feed_normalized_us": 1000 + i,
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
        assert feed_ingestion["median_us"] == 49.5
        assert feed_ingestion["p95_us"] >= 90
        assert feed_ingestion["p99_us"] >= 95

    def test_percentile_with_single_record(self):
        """Test percentile calculation with only one record."""
        timing_collector.configure(enabled=True)

        timing_collector.record_timing(
            "single",
            {
                "feed_received_us": 1000,
                "feed_normalized_us": 1500,
                "persistence_completed_us": 2000,
            },
        )

        metrics = timing_collector.get_metrics()
        stage_latencies = metrics["stage_latencies"]
        feed_ingestion = next(
            (s for s in stage_latencies if s["name"] == "feed_ingestion"), None
        )

        assert feed_ingestion["count"] == 1
        assert feed_ingestion["p95_us"] == 500
        assert feed_ingestion["p99_us"] == 500
