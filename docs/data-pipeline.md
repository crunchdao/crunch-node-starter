# Data Pipeline

The coordinator node runs a continuous pipeline that transforms live market data into ranked leaderboard entries and on-chain checkpoints.

## End-to-End Flow

```mermaid
sequenceDiagram
    participant Feed as Data Feed<br/>(Pyth/Binance)
    participant FDW as feed-data-worker
    participant DB as PostgreSQL
    participant PW as predict-worker
    participant MO as Model Orchestrator
    participant SW as score-worker
    participant CW as checkpoint-worker

    Feed->>FDW: WebSocket / REST data
    FDW->>DB: INSERT feed_records
    FDW->>DB: pg_notify('feed_data')
    DB-->>PW: LISTEN notification

    PW->>DB: READ latest feed data
    PW->>MO: gRPC predict(subject, horizon, step)
    MO-->>PW: inference_output dict
    PW->>DB: INSERT input (dumb log)
    PW->>DB: INSERT prediction (PENDING)

    Note over SW: Polls every score_interval_seconds

    SW->>DB: SELECT predictions WHERE status=PENDING<br/>AND resolvable_at <= now()
    
    alt resolve_horizon_seconds = 0 (immediate)
        SW->>DB: Ground truth from input.raw_data
    else resolve_horizon_seconds > 0 (deferred)
        SW->>DB: Fetch feed_records in time window
        SW->>SW: resolve_ground_truth(records)
    end

    SW->>SW: scoring_function(prediction, ground_truth)
    SW->>DB: INSERT score
    SW->>DB: UPDATE prediction status → SCORED
    SW->>SW: aggregate_snapshot(scores in period)
    SW->>DB: INSERT/UPDATE snapshot
    SW->>SW: Build Merkle tree over cycle snapshots
    SW->>DB: Rebuild leaderboard (windowed averages)

    Note over CW: Runs every checkpoint_interval_seconds

    CW->>DB: READ snapshots since last checkpoint
    CW->>CW: Rank models, build_emission()
    CW->>CW: Build Merkle root over cycle roots
    CW->>DB: INSERT checkpoint (PENDING)
    CW->>CW: Submit to chain
    CW->>DB: UPDATE checkpoint → SUBMITTED
```

## Pipeline Stages

### Stage 1: Feed Ingestion

The **feed-data-worker** connects to external data sources and writes normalized records:

```mermaid
graph LR
    A["Pyth Network<br/>WebSocket"] --> N["Normalize to<br/>FeedDataRecord"]
    B["Binance<br/>WebSocket"] --> N
    N --> DB["feed_records table<br/>(source, subject, kind,<br/>granularity, ts_event, values)"]
    DB --> NOTIFY["pg_notify<br/>→ predict-worker"]
```

Feed records have four generic dimensions:
- **source** — `pyth`, `binance`, etc.
- **subject** — `BTC`, `ETHUSDT`, etc.
- **kind** — `tick`, `candle`, `depth`, `funding`
- **granularity** — `1s`, `1m`, `5m`, `1h`

### Stage 2: Prediction

The **predict-worker** reacts to feed events and dispatches predictions:

1. Reads latest feed data from the database
2. Calls each registered model via gRPC through the model orchestrator
3. Stores the raw feed data as an `InputRecord` (dumb log — never updated)
4. Stores each model's response as a `PredictionRecord` with:
   - `scope_key` — which prediction config triggered this
   - `scope_jsonb` — full scope context (subject, step_seconds, etc.)
   - `resolvable_at` — when ground truth can be resolved
   - `inference_output_jsonb` — the model's raw output

### Stage 3: Scoring

The **score-worker** resolves predictions and computes scores:

```mermaid
graph TD
    A["Pending Predictions<br/>resolvable_at ≤ now()"] --> B{resolve_horizon}
    B -->|"= 0 (immediate)"| C["Ground truth from<br/>InputRecord.raw_data"]
    B -->|"> 0 (deferred)"| D["Fetch feed_records<br/>in time window"]
    D --> E["resolve_ground_truth<br/>(feed_records)"]
    C --> F["scoring_function<br/>(prediction, ground_truth)"]
    E --> F
    F --> G["ScoreRecord<br/>(result matches score_type)"]
    G --> H["aggregate_snapshot<br/>(scores in period)"]
    H --> I["SnapshotRecord<br/>(per-model period summary)"]
    I --> J["Merkle tree<br/>(cycle commit)"]
    I --> K["Rebuild Leaderboard<br/>(windowed averages)"]
```

### Stage 4: Checkpointing

The **checkpoint-worker** periodically aggregates snapshots into on-chain checkpoints:

1. Reads all snapshots since the last checkpoint
2. Ranks models using the leaderboard's `ranking_key`
3. Calls `build_emission()` to compute reward distribution
4. Builds a Merkle root over all cycle roots for tamper evidence
5. Submits the `EmissionCheckpoint` to the blockchain
6. Updates checkpoint status: `PENDING → SUBMITTED → CLAIMABLE → PAID`

## Status Lifecycles

```mermaid
stateDiagram-v2
    [*] --> PENDING: prediction created
    PENDING --> SCORED: scoring succeeded
    PENDING --> FAILED: scoring error
    PENDING --> ABSENT: no ground truth

    state Checkpoint {
        [*] --> CP_PENDING: checkpoint created
        CP_PENDING --> SUBMITTED: tx submitted
        SUBMITTED --> CLAIMABLE: tx confirmed
        CLAIMABLE --> PAID: rewards claimed
    }
```

## Timing

```
t=0          t=interval        t=interval+horizon     t=interval+horizon+score_interval
 │               │                    │                         │
 │  Feed data    │  predict()         │  resolvable_at          │  score()
 │  arrives      │  called            │  reached                │  runs
 └───────────────┴────────────────────┴─────────────────────────┘
```

- **`prediction_interval_seconds`** — how often models are called (e.g. every 15s)
- **`resolve_horizon_seconds`** — delay before scoring (0 = immediate, 60 = wait 1 minute for ground truth)
- **`score_interval_seconds`** — how often the score worker polls (auto-set to min(60, checkpoint_interval))
- **`checkpoint_interval_seconds`** — how often checkpoints are created (e.g. weekly)
