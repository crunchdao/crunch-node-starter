# Pipeline Performance Instrumentation - Implementation Plan

**Design Reference:** `docs/plans/2026-03-04-pipeline-performance-instrumentation-design.md`  
**Estimated Effort:** 2-3 days  
**Dependencies:** None (pure addition to existing codebase)

## Overview

Implement timing instrumentation across the coordinator pipeline to measure latency bottlenecks. This is Phase 1 of the sub-second latency optimization project.

## Implementation Phases

### Phase 1.1: Core Infrastructure (Day 1 Morning)

**1.1.1: TimingCollector Implementation**
```bash
# Create new module
touch crunch_node/metrics/__init__.py
touch crunch_node/metrics/timing.py
```

**Files to create/modify:**
- `crunch_node/metrics/timing.py` - TimingCollector class
- `crunch_node/metrics/__init__.py` - Exports

**Implementation tasks:**
- [ ] Create TimingCollector singleton with ring buffer
- [ ] Add thread-safe collection methods 
- [ ] Implement statistical analysis (percentiles, averages)
- [ ] Add enable/disable functionality
- [ ] Write unit tests for TimingCollector

**1.1.2: Configuration Integration**
**Files to modify:**
- `crunch_node/crunch_config.py` - Add timing config fields

**Implementation tasks:**
- [ ] Add `timing_metrics_enabled: bool = False`
- [ ] Add `timing_buffer_size: int = 10000`  
- [ ] Add `timing_endpoint_enabled: bool = True`
- [ ] Update config validation if needed

**1.1.3: HTTP Endpoint**
**Files to modify:**
- `crunch_node/workers/report_worker.py` - Add timing endpoint
- `crunch_node/schemas/` - Add timing response schemas

**Implementation tasks:**
- [ ] Create `/timing-metrics` FastAPI endpoint
- [ ] Add response schemas for timing data
- [ ] Wire TimingCollector to endpoint
- [ ] Add endpoint tests

**Validation:**
- [ ] Start coordinator with timing enabled
- [ ] Verify endpoint returns empty metrics initially
- [ ] Verify endpoint can be disabled via config

### Phase 1.2: Feed Worker Instrumentation (Day 1 Afternoon)

**Files to modify:**
- `crunch_node/workers/predict_worker.py` (feed ingestion)
- `crunch_node/entities/feed.py` (FeedDataRecord)
- `crunch_node/feeds/` - All feed implementations

**Implementation tasks:**
- [ ] Add `_timing` field to FeedDataRecord dataclass
- [ ] Instrument feed receive timestamp
- [ ] Instrument normalization timestamp  
- [ ] Instrument database write + pg_notify timestamp
- [ ] Update feed provider implementations
- [ ] Add timing to FeedDataService

**Code locations:**
```python
# In FeedDataService.save_records()
for record in records:
    record._timing = {
        "feed_received_us": record.received_timestamp,
        "feed_normalized_us": time.perf_counter_ns() // 1000,
    }
    # ... database operations ...
    record._timing["feed_persisted_us"] = time.perf_counter_ns() // 1000
```

**Validation:**
- [ ] Verify feed records contain timing data
- [ ] Check timing data flows to PostgreSQL
- [ ] Test with multiple feed sources (Pyth, Binance)

### Phase 1.3: Predict Worker Instrumentation (Day 2)

**Files to modify:**
- `crunch_node/workers/predict_worker.py`
- `crunch_node/services/realtime_predict.py`
- `crunch_node/entities/prediction.py` (InputRecord, PredictionRecord)

**Implementation tasks:**
- [ ] Add `_timing` field to InputRecord dataclass
- [ ] Add `_timing` field to PredictionRecord dataclass
- [ ] Instrument pg_notify reception in predict worker
- [ ] Instrument database read timing
- [ ] Instrument model dispatch timing in RealtimePredictService
- [ ] Instrument model completion timing
- [ ] Instrument callback execution timing
- [ ] Instrument persistence timing
- [ ] Add collection point in `_save()` method

**Key code locations:**
```python
# In RealtimePredictService.run_once()
inp._timing = {
    "notify_received_us": notify_timestamp,
    "data_loaded_us": time.perf_counter_ns() // 1000,
}

# In _predict_all_configs()
prediction._timing = inp._timing.copy()
prediction._timing.update({
    "models_dispatched_us": time.perf_counter_ns() // 1000,
})

# After model calls complete
prediction._timing["models_completed_us"] = time.perf_counter_ns() // 1000

# Around post_predict_hook
prediction._timing["callback_started_us"] = time.perf_counter_ns() // 1000
if self.post_predict_hook:
    predictions = self.post_predict_hook(predictions, inp, now)
prediction._timing["callback_completed_us"] = time.perf_counter_ns() // 1000

# In _save() method - COLLECTION POINT
def _save(self, predictions):
    for prediction in predictions:
        prediction._timing["persistence_completed_us"] = time.perf_counter_ns() // 1000
        timing_collector.record_timing(prediction.id, prediction._timing)
    # ... existing save logic ...
```

**Validation:**
- [ ] Verify complete timing traces in TimingCollector
- [ ] Test with and without post_predict_hook
- [ ] Verify timing data accuracy with manual timing
- [ ] Check collection point works correctly

### Phase 1.4: Analysis & Validation (Day 3)

**Implementation tasks:**
- [ ] Add comprehensive timing analysis to HTTP endpoint
- [ ] Implement stage latency calculations
- [ ] Add percentile calculations (p50, p95, p99)
- [ ] Add end-to-end latency calculation
- [ ] Create timing visualization/debugging tools
- [ ] Performance overhead testing
- [ ] Integration testing with multiple feed sources

**Testing checklist:**
- [ ] Verify < 1% performance overhead when enabled
- [ ] Verify zero overhead when disabled  
- [ ] Test timing accuracy under load
- [ ] Test with different feed sources and models
- [ ] Validate timing data flows through complete pipeline
- [ ] Test endpoint performance under concurrent access

**Analysis tools:**
```python
# Add to timing endpoint
def analyze_latencies(timing_records):
    stages = ["feed_ingestion", "prediction_trigger", "model_dispatch", 
              "callback_execution", "persistence", "end_to_end"]
    
    analysis = {}
    for stage in stages:
        latencies = [calc_stage_latency(record, stage) for record in timing_records]
        analysis[f"{stage}_p50_us"] = np.percentile(latencies, 50)
        analysis[f"{stage}_p95_us"] = np.percentile(latencies, 95)  
        analysis[f"{stage}_p99_us"] = np.percentile(latencies, 99)
        
    return analysis
```

## File Structure

```
crunch_node/
├── metrics/
│   ├── __init__.py          # New
│   └── timing.py            # New - TimingCollector
├── entities/
│   ├── feed.py              # Modified - add _timing to FeedDataRecord
│   └── prediction.py        # Modified - add _timing to InputRecord, PredictionRecord  
├── workers/
│   ├── predict_worker.py    # Modified - feed ingestion + timing instrumentation
│   ├── predict_worker.py    # Modified - add timing instrumentation
│   └── report_worker.py     # Modified - add timing endpoint
├── services/
│   └── realtime_predict.py  # Modified - add timing + collection point
├── schemas/
│   └── timing.py            # New - timing response schemas
└── crunch_config.py         # Modified - add timing config
```

## Testing Strategy

**Unit Tests:**
- TimingCollector functionality
- Timing data accuracy  
- Configuration handling
- HTTP endpoint responses

**Integration Tests:**
- End-to-end timing flow
- Multiple feed source handling
- Performance overhead validation
- Concurrent access testing

**Load Tests:**
- Verify minimal overhead under high throughput
- Test ring buffer behavior when full
- Validate timing accuracy under load

## Success Metrics

1. **Functional:**
   - All pipeline stages have timing data
   - HTTP endpoint returns accurate metrics
   - Configurable enable/disable works

2. **Performance:**
   - < 1% overhead when enabled
   - Zero overhead when disabled
   - Ring buffer memory usage stays bounded

3. **Accuracy:**
   - Timing data matches manual measurements
   - Stage breakdowns sum to end-to-end latency
   - Percentile calculations are reasonable

4. **Operational:**
   - Safe for production deployment
   - No impact on existing functionality
   - Easy to analyze bottleneck patterns

## Risk Mitigation

**Performance Risk:** 
- Implement feature flag for instant disable
- Thorough performance testing before production

**Memory Risk:**
- Ring buffer with hard size limit
- Monitor memory usage in tests

**Accuracy Risk:**
- Cross-validate with external timing tools
- Test timing accuracy under various load conditions

**Integration Risk:**
- Incremental rollout by phase
- Extensive testing with existing feed sources

## Next Steps After Completion

With instrumentation complete, the data will guide Phase 2 optimization efforts:
1. Identify actual bottleneck stages from real timing data
2. Design hot path architecture based on findings
3. Validate optimization impact with before/after metrics
4. Plan Rust implementation for maximum performance