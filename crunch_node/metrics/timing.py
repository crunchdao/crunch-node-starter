"""
Performance timing aggregation for the coordinator pipeline.
"""

import statistics
from typing import Any


def aggregate_timing_from_predictions(predictions: list) -> dict[str, Any]:
    """Aggregate timing metrics from prediction records stored in the database."""
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
        ("notify_latency", "notify_sent_us", "notify_received_us"),
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
                    "p95_us": _percentile(latencies, 95),
                    "p99_us": _percentile(latencies, 99),
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
                "p95_us": _percentile(e2e_latencies, 95),
                "p99_us": _percentile(e2e_latencies, 99),
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


def _percentile(data: list[float], p: float) -> float:
    """Calculate percentile of a list of values."""
    if not data:
        return 0.0
    if len(data) < 2:
        return data[0]
    return statistics.quantiles(data, n=100)[p - 1] if p <= 100 else max(data)
