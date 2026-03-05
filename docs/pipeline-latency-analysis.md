# Pipeline Latency Analysis

Analysis of the prediction pipeline latency, architectural tradeoffs, and optimization opportunities.

## Current Architecture

```
feed-data-worker                    predict-worker
      |                                   |
  [receive feed]                          |
      |                                   |
  [normalize]                             |
      |                                   |
  [persist to DB] ----NOTIFY----> [receive notify]
                                          |
                                    [load from DB]
                                          |
                                    [gRPC tick models]
                                          |
                                    [gRPC predict]
                                          |
                                    [callback]
                                          |
                                    [persist prediction]
```

## Current Performance Breakdown

Measured with cross-process wall-clock timing (`time.time_ns()`):

| Stage | % of Total | Mean | Median | Description |
|-------|-----------|------|--------|-------------|
| feed_ingestion | 0.1% | 0.0ms | 0.0ms | Receive and parse feed data |
| feed_persistence | 21.3% | 7.7ms | 8.2ms | Write feed record to PostgreSQL |
| notify_latency | 33.2% | 12.1ms | 12.3ms | PostgreSQL NOTIFY across processes |
| data_loading | 16.1% | 5.9ms | 6.0ms | Query feed records from DB |
| pre_model | 17.9% | 6.5ms | 6.6ms | Config query, scope building, gRPC tick |
| model_execution | 2.5% | 0.9ms | 0.9ms | gRPC predict call to models |
| post_model | 8.9% | 3.2ms | 3.2ms | Result processing |
| callback_execution | 0.0% | 0.0ms | 0.0ms | Post-predict hook (if configured) |
| prediction_persistence | 0.0% | 0.0ms | 0.0ms | Write prediction to DB |
| **end_to_end** | **100%** | **36.4ms** | **37.2ms** | **Total pipeline latency** |

## Optimization Opportunities

### Option 1: Combined Worker (In-Process Hot Path)

Combine feed ingestion and prediction into a single process:

| Stage | Current | In-Process | Savings |
|-------|---------|------------|---------|
| feed_ingestion | 0.0ms | 0.0ms | - |
| feed_persistence | 7.7ms | 0ms | async/background |
| notify_latency | 12.1ms | 0ms | direct function call |
| data_loading | 5.9ms | 0ms | data in memory |
| pre_model | 6.5ms | ~4ms | cache configs in memory |
| model_execution | 0.9ms | 0.9ms | same |
| post_model | 3.2ms | 3.2ms | same |

**Estimated improvement: ~36ms → ~10ms (70-75% reduction)**

#### Hybrid Architecture

Keep hot path in-process, fan out async for other consumers:

```
Single Process (hot path, ~10ms):
  feed receive → normalize → predict → callback

Async Fan-out (cold path):
  → persist feed record (background)
  → persist prediction (background)
  → NOTIFY other consumers (scoring, monitoring, UI)
```

Benefits:
- 10ms latency for predictions
- Multi-consumer support via async fanout
- Persistence for replay/debugging (non-blocking)
- Fault isolation for non-critical consumers

Tradeoff:
- If hot-path process crashes mid-prediction, that cycle is lost

### Option 2: Rust Hot Path

Reimplement the hot path in Rust:

| Component | Python | Rust | Reason |
|-----------|--------|------|--------|
| Feed parse/normalize | 0.1ms | 0.02ms | serde vs Pydantic |
| Scope building | 1ms | 0.1ms | struct vs dict |
| gRPC tick call | 2-3ms | 2-3ms | same (network I/O) |
| gRPC predict call | 0.9ms | 0.9ms | same (network I/O) |
| Result processing | 2ms | 0.2ms | zero-copy, no GIL |
| Callback dispatch | 1ms | 0.1ms | direct call |

**Estimated improvement: ~10ms → ~6-7ms (30-40% reduction)**

Rust advantages:
- No GIL contention under load
- Predictable latency (no GC pauses)
- Memory efficiency for high-frequency data
- Bigger gains if models are embedded (ONNX runtime)

Rust disadvantages:
- Most time is network I/O (gRPC) - Rust can't speed this up
- Significant rewrite effort
- Harder to iterate/debug

**Verdict:** For ~3-4ms savings on in-process path, probably not worth it unless:
- Models are embedded in-process
- Need p99 latency guarantees
- Running at very high throughput

## Why Current Separation Exists

Benefits of separate feed/predict workers:

1. **Independent scaling** - scale based on different bottlenecks
2. **Fault isolation** - crashes don't cascade
3. **Deployment flexibility** - update independently
4. **Multi-consumer pattern** - multiple services can consume feed data
5. **Observability** - clear ownership of metrics/logs

Note: "CPU vs I/O isolation" is NOT a benefit here since predict worker calls external models via gRPC (I/O-bound, not CPU-bound).

## Recommendations

1. **Quick win:** Combined worker with async fanout → ~10ms latency
2. **If needed:** Rust hot path → ~6-7ms latency
3. **For lowest latency:** Embed models in-process (ONNX) + Rust → sub-millisecond possible

## Timing Implementation Notes

Cross-process timing uses `time.time_ns()` (wall clock) instead of `time.perf_counter_ns()` (process-relative monotonic counter).

Feed timing is passed through PostgreSQL NOTIFY payload as JSON:
```json
{
  "feed_received_us": 1772656460084264,
  "feed_normalized_us": 1772656460084300,
  "feed_persisted_us": 1772656460094500
}
```

This ensures accurate cross-process latency measurement (~120 bytes payload, negligible overhead).
