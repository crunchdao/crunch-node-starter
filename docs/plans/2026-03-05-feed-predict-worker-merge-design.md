# Feed-Predict Worker Merge Design

**Date:** 2026-03-05
**Status:** Draft
**Goal:** Reduce prediction pipeline latency from ~37ms to ~10ms by merging feed and predict workers

## Background

PR #20 instrumented the pipeline and identified that 70% of latency comes from inter-process communication:

| Stage | Current | After Merge |
|-------|---------|-------------|
| feed_persistence | 8ms | 0ms (async) |
| notify_latency | 12ms | 0ms (eliminated) |
| data_loading | 6ms | 0ms (in-memory) |
| pre_model | 6ms | ~4ms (cached) |
| model_execution | 1ms | 1ms |
| post_model | 3ms | 3ms |
| **Total** | **~37ms** | **~10ms** |

## Decision: Complete Replacement

Investigated keeping separate workers as an option. Found no real benefits:

- **Independent scaling**: Both workers are I/O-bound, single-instance
- **Fault isolation**: Pipeline is already tightly coupled (either crash = broken)
- **Deployment flexibility**: Same Docker image, same package version
- **Observability**: Cross-process timing is harder, not easier

Conclusion: Merge completely. No config flag for separate mode.

## Architecture

### Before

```
feed-data-worker              predict-worker
     │                             │
[subscribe to feed]                │
     │                             │
[persist to DB] ──NOTIFY──► [wait for notify]
                                   │
                            [query window from DB]
                                   │
                            [predict]
```

### After

```
predict-worker (combined)
     │
[subscribe to feed]
     │
[update in-memory window]
     │
[build aggregated input]
     │
[predict]  ←── hot path (~10ms)
     │
[async persist feed record]  ←── cold path (background)
```

## Components

### 1. FeedWindow (new, ~40 lines)

In-memory rolling window of feed records per subject.

```python
class FeedWindow:
    _windows: dict[str, deque[FeedDataRecord]]  # subject → recent records

    def append(record: FeedDataRecord) -> None
    def get_candles(subject: str) -> list[dict]
    def load_from_db(repository, settings) -> None  # startup initialization
```

### 2. PredictSink (new, ~50 lines)

Implements `FeedSink` protocol. Receives feed records, triggers predictions.

```python
class PredictSink(FeedSink):
    async def on_record(self, record: FeedDataRecord) -> None:
        # 1. Update in-memory window
        self.feed_window.append(record)

        # 2. Build aggregated input
        raw_input = self._build_input(record.subject)

        # 3. Hot path: predict
        await self.predict_service.run_once(raw_input=raw_input, feed_timing=timing)

        # 4. Cold path: async persist
        asyncio.create_task(self._persist_async(record))
```

### 3. FeedDataService (modified, ~5 lines changed)

Add optional `sink` parameter to `__init__`. If provided, use instead of default `_RepositorySink`.

### 4. predict_worker.py (rewritten)

Orchestrates combined flow:

```python
async def main():
    predict_service = build_predict_service()
    await predict_service.init_runner()

    feed_window = FeedWindow(max_size=120)
    feed_window.load_from_db(feed_repository, settings)

    sink = PredictSink(predict_service, feed_repository, feed_window)
    feed_service = FeedDataService(settings, feed_repository, sink=sink)

    await feed_service.run()  # handles backfill, subscription, retention
```

## Data Persistence

All data persisted as before, same schema:

| Table | Data | Change |
|-------|------|--------|
| `feed_records` | Raw ticks/candles | Async instead of sync |
| `inputs` | Aggregated windows | No change |
| `predictions` | Model outputs | No change |
| `scores`, etc. | Scoring results | No change (score_worker unchanged) |

## Files Changed

| File | Change |
|------|--------|
| `crunch_node/services/feed_data.py` | Add optional `sink` parameter |
| `crunch_node/services/feed_window.py` | New file |
| `crunch_node/services/predict_sink.py` | New file |
| `crunch_node/workers/predict_worker.py` | Rewrite to orchestrate combined flow |
| `crunch_node/workers/feed_data_worker.py` | Delete |
| `docker-compose.yml` | Remove feed-data-worker service |
| `scaffold/node/docker-compose.yml` | Remove feed-data-worker service |

## Testing

- Unit tests for FeedWindow (append, get_candles, load_from_db)
- Unit tests for PredictSink (mock predict_service, verify calls)
- Integration test: feed record → prediction persisted
- Latency verification: measure e2e timing, confirm ~10ms target

## Rollback

If issues arise, revert the PR. The old architecture still works — just re-add feed-data-worker to docker-compose.
