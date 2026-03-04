"""
Performance timing instrumentation for the coordinator pipeline.

This module provides a thread-safe timing collector that instruments the pipeline
from feed ingestion through prediction completion to identify latency bottlenecks.
"""

import statistics
import threading
import time
from collections import deque
from typing import Any


class TimingCollector:
    """
    Thread-safe collector for pipeline timing measurements.

    Uses a ring buffer to store timing data with configurable size limits.
    Provides statistical analysis for identifying performance bottlenecks.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self._buffer: deque = deque()
        self._enabled = False
        self._buffer_size = 10000
        self._data_lock = threading.Lock()
        self._initialized = True

    def configure(self, enabled: bool = False, buffer_size: int = 10000):
        """Configure the timing collector."""
        with self._data_lock:
            self._enabled = enabled
            self._buffer_size = buffer_size
            # Resize buffer if needed
            if len(self._buffer) > buffer_size:
                # Keep the most recent entries
                self._buffer = deque(
                    list(self._buffer)[-buffer_size:], maxlen=buffer_size
                )
            else:
                self._buffer = deque(self._buffer, maxlen=buffer_size)

    @property
    def enabled(self) -> bool:
        """Check if timing collection is enabled."""
        return self._enabled

    @property
    def buffer_size(self) -> int:
        """Get current buffer size."""
        with self._data_lock:
            return len(self._buffer)

    def record_timing(self, prediction_id: str, timing_data: dict[str, Any]):
        """
        Record timing data for a prediction.

        Args:
            prediction_id: Unique identifier for the prediction
            timing_data: Dictionary containing timing measurements in microseconds
        """
        if not self._enabled:
            return

        record = {
            "prediction_id": prediction_id,
            "timestamp": time.time(),
            **timing_data,
        }

        with self._data_lock:
            self._buffer.append(record)

    def get_recent(self, n: int = 100) -> list[dict[str, Any]]:
        """Get the most recent N timing records."""
        with self._data_lock:
            if not self._buffer:
                return []
            return list(self._buffer)[-n:]

    def get_all_records(self) -> list[dict[str, Any]]:
        """Get all timing records in the buffer."""
        with self._data_lock:
            return list(self._buffer)

    def clear(self):
        """Clear all timing records."""
        with self._data_lock:
            self._buffer.clear()

    def get_metrics(self) -> dict[str, Any]:
        """
        Calculate comprehensive timing metrics.

        Returns:
            Dictionary with stage latencies, percentiles, and summary statistics
        """
        records = self.get_all_records()
        if not records:
            return {
                "enabled": self._enabled,
                "buffer_size": 0,
                "total_records": 0,
                "stage_latencies": [],
                "recent_samples": [],
            }

        # Calculate stage latencies
        stage_latencies = self._calculate_stage_latencies(records)

        return {
            "enabled": self._enabled,
            "buffer_size": len(records),
            "total_records": len(records),
            "stage_latencies": stage_latencies,
            "recent_samples": self.get_recent(10),  # Last 10 for debugging
        }

    def _calculate_stage_latencies(
        self, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Calculate latency statistics for each pipeline stage."""
        stage_definitions = [
            ("feed_ingestion", "feed_received_us", "feed_normalized_us"),
            ("feed_persistence", "feed_normalized_us", "feed_persisted_us"),
            ("notify_latency", "feed_persisted_us", "notify_received_us"),
            ("data_loading", "notify_received_us", "data_loaded_us"),
            ("pre_model", "data_loaded_us", "models_dispatched_us"),
            ("model_execution", "models_dispatched_us", "models_completed_us"),
            ("post_model", "models_completed_us", "callback_started_us"),
            ("callback_execution", "callback_started_us", "callback_completed_us"),
            (
                "prediction_persistence",
                "callback_completed_us",
                "persistence_completed_us",
            ),
        ]

        e2e_latencies = []
        for record in records:
            start_time = record.get("feed_received_us")
            end_time = record.get("persistence_completed_us")
            if start_time is not None and end_time is not None:
                latency = end_time - start_time
                if latency >= 0:
                    e2e_latencies.append(latency)

        e2e_mean = statistics.mean(e2e_latencies) if e2e_latencies else None

        result = []

        for step, (stage_name, start_field, end_field) in enumerate(
            stage_definitions, start=1
        ):
            latencies = []

            for record in records:
                start_time = record.get(start_field)
                end_time = record.get(end_field)

                if start_time is not None and end_time is not None:
                    latency = end_time - start_time
                    if latency >= 0:
                        latencies.append(latency)

            if latencies:
                mean = statistics.mean(latencies)
                pct = round((mean / e2e_mean) * 100, 1) if e2e_mean else None
                result.append(
                    {
                        "name": stage_name,
                        "step": step,
                        "pct_of_total": pct,
                        "count": len(latencies),
                        "mean_us": mean,
                        "median_us": statistics.median(latencies),
                        "min_us": min(latencies),
                        "max_us": max(latencies),
                        "p95_us": self._percentile(latencies, 95),
                        "p99_us": self._percentile(latencies, 99),
                    }
                )
            else:
                result.append(
                    {
                        "name": stage_name,
                        "step": step,
                        "pct_of_total": None,
                        "count": 0,
                        "mean_us": None,
                        "median_us": None,
                        "min_us": None,
                        "max_us": None,
                        "p95_us": None,
                        "p99_us": None,
                    }
                )

        if e2e_latencies:
            result.append(
                {
                    "name": "end_to_end",
                    "step": "total",
                    "pct_of_total": 100.0,
                    "count": len(e2e_latencies),
                    "mean_us": e2e_mean,
                    "median_us": statistics.median(e2e_latencies),
                    "min_us": min(e2e_latencies),
                    "max_us": max(e2e_latencies),
                    "p95_us": self._percentile(e2e_latencies, 95),
                    "p99_us": self._percentile(e2e_latencies, 99),
                }
            )
        else:
            result.append(
                {
                    "name": "end_to_end",
                    "step": "total",
                    "pct_of_total": None,
                    "count": 0,
                    "mean_us": None,
                    "median_us": None,
                    "min_us": None,
                    "max_us": None,
                    "p95_us": None,
                    "p99_us": None,
                }
            )

        return result

    @staticmethod
    def _percentile(data: list[float], p: float) -> float:
        """Calculate percentile of a list of values."""
        if not data:
            return 0.0
        return statistics.quantiles(data, n=100)[p - 1] if p <= 100 else max(data)


# Global singleton instance
timing_collector = TimingCollector()


def get_timing_collector() -> TimingCollector:
    """Get the global timing collector instance."""
    return timing_collector


def aggregate_timing_from_predictions(predictions: list) -> dict[str, Any]:
    """
    Aggregate timing metrics from prediction records stored in the database.

    Extracts timing data from prediction.meta["timing"] and calculates
    stage latencies similar to TimingCollector.get_metrics().
    """
    if not predictions:
        return {
            "enabled": True,
            "buffer_size": 0,
            "total_records": 0,
            "stage_latencies": [],
            "recent_samples": [],
        }

    records = []
    for pred in predictions:
        timing = pred.meta.get("timing") if pred.meta else None
        if timing:
            records.append({"prediction_id": pred.id, **timing})

    if not records:
        return {
            "enabled": True,
            "buffer_size": 0,
            "total_records": 0,
            "stage_latencies": [],
            "recent_samples": [],
        }

    stage_definitions = [
        ("feed_ingestion", "feed_received_us", "feed_normalized_us"),
        ("feed_persistence", "feed_normalized_us", "feed_persisted_us"),
        ("notify_latency", "feed_persisted_us", "notify_received_us"),
        ("data_loading", "notify_received_us", "data_loaded_us"),
        ("pre_model", "data_loaded_us", "models_dispatched_us"),
        ("model_execution", "models_dispatched_us", "models_completed_us"),
        ("post_model", "models_completed_us", "callback_started_us"),
        ("callback_execution", "callback_started_us", "callback_completed_us"),
        ("prediction_persistence", "callback_completed_us", "persistence_completed_us"),
    ]

    e2e_latencies = []
    for record in records:
        start_time = record.get("feed_received_us")
        end_time = record.get("persistence_completed_us")
        if start_time is not None and end_time is not None:
            latency = end_time - start_time
            if latency >= 0:
                e2e_latencies.append(latency)

    e2e_mean = statistics.mean(e2e_latencies) if e2e_latencies else None

    stage_latencies = []
    for step, (stage_name, start_field, end_field) in enumerate(
        stage_definitions, start=1
    ):
        latencies = []
        for record in records:
            start_time = record.get(start_field)
            end_time = record.get(end_field)
            if start_time is not None and end_time is not None:
                latency = end_time - start_time
                if latency >= 0:
                    latencies.append(latency)

        if latencies:
            mean = statistics.mean(latencies)
            pct = round((mean / e2e_mean) * 100, 1) if e2e_mean else None
            stage_latencies.append(
                {
                    "name": stage_name,
                    "step": step,
                    "pct_of_total": pct,
                    "count": len(latencies),
                    "mean_us": mean,
                    "median_us": statistics.median(latencies),
                    "min_us": min(latencies),
                    "max_us": max(latencies),
                    "p95_us": statistics.quantiles(latencies, n=100)[94]
                    if len(latencies) >= 2
                    else latencies[0],
                    "p99_us": statistics.quantiles(latencies, n=100)[98]
                    if len(latencies) >= 2
                    else latencies[0],
                }
            )
        else:
            stage_latencies.append(
                {
                    "name": stage_name,
                    "step": step,
                    "pct_of_total": None,
                    "count": 0,
                    "mean_us": None,
                    "median_us": None,
                    "min_us": None,
                    "max_us": None,
                    "p95_us": None,
                    "p99_us": None,
                }
            )

    if e2e_latencies:
        stage_latencies.append(
            {
                "name": "end_to_end",
                "step": "total",
                "pct_of_total": 100.0,
                "count": len(e2e_latencies),
                "mean_us": e2e_mean,
                "median_us": statistics.median(e2e_latencies),
                "min_us": min(e2e_latencies),
                "max_us": max(e2e_latencies),
                "p95_us": statistics.quantiles(e2e_latencies, n=100)[94]
                if len(e2e_latencies) >= 2
                else e2e_latencies[0],
                "p99_us": statistics.quantiles(e2e_latencies, n=100)[98]
                if len(e2e_latencies) >= 2
                else e2e_latencies[0],
            }
        )
    else:
        stage_latencies.append(
            {
                "name": "end_to_end",
                "step": "total",
                "pct_of_total": None,
                "count": 0,
                "mean_us": None,
                "median_us": None,
                "min_us": None,
                "max_us": None,
                "p95_us": None,
                "p99_us": None,
            }
        )

    return {
        "enabled": True,
        "buffer_size": len(records),
        "total_records": len(records),
        "stage_latencies": stage_latencies,
        "recent_samples": records[-10:],
    }
