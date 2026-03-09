# Pipeline Performance Instrumentation Design

**Date:** 2026-03-04  
**Status:** Approved  
**Goal:** Add comprehensive timing instrumentation to identify pipeline bottlenecks before optimization

## Overview

Add timing instrumentation to the existing Crunch coordinator pipeline without changing core functionality. This is Phase 1 of a performance optimization initiative targeting sub-second latency for high-frequency trading scenarios.

## Background

Current pipeline: `Feed Sources → predict-worker (feed ingestion) → PostgreSQL → models → post_predict_hook → PostgreSQL`

Target: Sub-second end-to-end latency (feed ingestion to callback execution)

Strategy: Measure first, optimize second. This phase instruments the existing system to identify actual bottlenecks.

## Architecture

### Core Components

**1. Configuration (CrunchConfig)**
```python
timing_metrics_enabled: bool = False      # Global enable/disable
timing_buffer_size: int = 10000          # Ring buffer max entries  
timing_endpoint_enabled: bool = True     # Expose HTTP endpoint
```

**2. TimingCollector (Singleton)**
- Ring buffer (`collections.deque`) with configurable max size
- Thread-safe collection methods
- Statistical analysis (percentiles, averages, stage breakdowns)
- Recent sample access for debugging

**3. Data Structure Enhancement**
Add `_timing` field to existing structures:
- `FeedDataRecord._timing`
- `InputRecord._timing` 
- `PredictionRecord._timing`

**4. Collection Point**
Single collection in `RealtimePredictService._save()` after post_predict_hook execution.

**5. HTTP Endpoint**
`/timing-metrics` in report-worker FastAPI for analysis.

### Timing Stages

**Stage 1: Feed Ingestion**
- `feed_received_us`: Raw data received from source
- `feed_normalized_us`: Data normalized to FeedDataRecord  
- `feed_persisted_us`: Written to PostgreSQL + pg_notify sent

**Stage 2: Prediction Trigger**
- `notify_received_us`: predict-worker receives pg_notify
- `data_loaded_us`: Latest feed data read from database

**Stage 3: Model Dispatch** 
- `models_dispatched_us`: All gRPC calls initiated
- `models_completed_us`: All model responses received

**Stage 4: Post-Predict Callback**
- `callback_started_us`: post_predict_hook execution begins
- `callback_completed_us`: post_predict_hook execution complete

**Stage 5: Persistence**
- `persistence_completed_us`: All database writes finished

### Data Flow

Timing metadata travels with data structures:

```python
# predict-worker (feed ingestion)
feed_record._timing = {
    "feed_received_us": time.perf_counter_ns() // 1000,
    "feed_normalized_us": ...,
    "feed_persisted_us": ...
}

# predict-worker
input_record._timing = feed_record._timing.copy()
input_record._timing.update({
    "notify_received_us": ..., 
    "data_loaded_us": ...
})

# RealtimePredictService
prediction._timing = input_record._timing.copy()
prediction._timing.update({
    "models_dispatched_us": ...,
    "models_completed_us": ..., 
    "callback_started_us": ...,
    "callback_completed_us": ...,
    "persistence_completed_us": ...
})

# Collection point in _save()
timing_collector.record_timing(prediction.id, prediction._timing)
```

### TimingCollector API

```python
class TimingCollector:
    def __init__(self, buffer_size: int):
        self._buffer = collections.deque(maxlen=buffer_size)
        self._enabled = False
        self._lock = threading.Lock()
    
    def record_timing(self, prediction_id: str, timing_data: dict):
        if self._enabled:
            with self._lock:
                self._buffer.append({
                    "prediction_id": prediction_id,
                    "timestamp": time.time(),
                    **timing_data
                })
    
    def get_metrics(self) -> dict:
        # Return percentiles, averages, stage breakdowns
        
    def get_recent(self, n: int) -> list:
        # Return last N timing records
```

### HTTP Endpoint

```python
@router.get("/timing-metrics")
async def get_timing_metrics():
    return {
        "enabled": timing_collector.enabled,
        "buffer_size": len(timing_collector._buffer),
        "stage_latencies": {
            "feed_ingestion_p99_us": ...,
            "prediction_trigger_p99_us": ...,
            "model_dispatch_p99_us": ..., 
            "callback_execution_p99_us": ...,
            "persistence_p99_us": ...,
            "end_to_end_p99_us": ...
        },
        "recent_samples": timing_collector.get_recent(100)
    }
```

## Implementation Strategy

### Phase 1.1: Basic Infrastructure
- Create TimingCollector singleton
- Add configuration fields to CrunchConfig
- Add HTTP endpoint to report-worker
- Test with simple end-to-end timing

### Phase 1.2: Feed Worker Instrumentation  
- Add timing to predict-worker feed ingestion
- Instrument FeedDataRecord with _timing field
- Test feed ingestion stages

### Phase 1.3: Predict Worker Instrumentation
- Add timing to predict-worker notification handling
- Instrument RealtimePredictService stages
- Add callback timing around post_predict_hook
- Implement collection point in _save()

### Phase 1.4: Analysis & Validation
- Verify timing data accuracy
- Test with multiple feed sources  
- Validate minimal performance overhead when enabled
- Analyze bottleneck patterns

## Success Criteria

1. **Complete visibility**: Every prediction has end-to-end timing trace
2. **Stage breakdown**: Can identify which stage is the bottleneck
3. **Configurable**: Can enable/disable without code changes
4. **Low overhead**: < 1% performance impact when enabled
5. **Production ready**: Safe to run in live crunch nodes

## Future Phases

This instrumentation provides baseline metrics to guide:
- **Phase 2**: Hot path optimization (in-process, bypass PostgreSQL)
- **Phase 3**: Rust implementation for maximum performance
- **Phase 4**: Architecture changes based on bottleneck analysis

## Non-Goals

- No performance optimizations in this phase
- No architectural changes to existing pipeline
- No new storage systems or databases
- No changes to external model interfaces

This design focuses purely on measurement to enable data-driven optimization decisions.