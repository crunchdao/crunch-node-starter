"""
Schema definitions for timing metrics API responses.
"""

from typing import Any

from pydantic import BaseModel


class StageLatencyStats(BaseModel):
    """Statistical measurements for a pipeline stage."""

    count: int
    mean_us: float | None
    median_us: float | None
    min_us: float | None
    max_us: float | None
    p95_us: float | None
    p99_us: float | None


class TimingMetricsResponse(BaseModel):
    """Response schema for timing metrics endpoint."""

    enabled: bool
    buffer_size: int
    total_records: int
    stage_latencies: dict[str, StageLatencyStats]
    recent_samples: list[dict[str, Any]]


class TimingRecord(BaseModel):
    """Individual timing record for debugging."""

    prediction_id: str
    timestamp: float
    # Optional timing fields - all in microseconds
    feed_received_us: float | None = None
    feed_normalized_us: float | None = None
    feed_persisted_us: float | None = None
    notify_received_us: float | None = None
    data_loaded_us: float | None = None
    models_dispatched_us: float | None = None
    models_completed_us: float | None = None
    callback_started_us: float | None = None
    callback_completed_us: float | None = None
    persistence_completed_us: float | None = None
