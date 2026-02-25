# Database Schema

All data is stored in PostgreSQL using SQLModel (SQLAlchemy + Pydantic). JSONB columns store typed data that passes through Pydantic `model_validate()` / `model_dump()` at every boundary.

## Entity Relationship Diagram

```mermaid
erDiagram
    feed_records {
        string id PK
        string source
        string subject
        string kind
        string granularity
        bigint ts_event
        jsonb values
        jsonb metadata
        datetime received_at
    }

    inputs {
        string id PK
        jsonb raw_data_jsonb
        datetime received_at
    }

    models {
        string id PK
        string name
        string deployment_identifier
        string player_id
        string player_name
        jsonb overall_score_jsonb
        jsonb scores_by_scope_jsonb
        jsonb meta_jsonb
        datetime created_at
        datetime updated_at
    }

    scheduled_prediction_configs {
        string id PK
        string scope_key
        jsonb scope_template_jsonb
        jsonb schedule_jsonb
        boolean active
        integer order
        jsonb meta_jsonb
    }

    predictions {
        string id PK
        string input_id FK
        string model_id FK
        string prediction_config_id FK
        string scope_key
        jsonb scope_jsonb
        string status
        float exec_time_ms
        jsonb inference_output_jsonb
        jsonb meta_jsonb
        datetime performed_at
        datetime resolvable_at
    }

    scores {
        string id PK
        string prediction_id FK
        jsonb result_jsonb
        boolean success
        string failed_reason
        datetime scored_at
    }

    snapshots {
        string id PK
        string model_id FK
        datetime period_start
        datetime period_end
        integer prediction_count
        jsonb result_summary_jsonb
        jsonb meta_jsonb
        string content_hash
        datetime created_at
    }

    leaderboards {
        string id PK
        jsonb entries_jsonb
        jsonb meta_jsonb
        datetime created_at
    }

    checkpoints {
        string id PK
        datetime period_start
        datetime period_end
        string status
        jsonb entries_jsonb
        jsonb meta_jsonb
        string merkle_root
        string tx_hash
        datetime submitted_at
        datetime created_at
    }

    merkle_cycles {
        string id PK
        string cycle_root
        string prev_cycle_root
        string checkpoint_id FK
        integer snapshot_count
        datetime created_at
    }

    merkle_nodes {
        string id PK
        string cycle_id FK
        string hash
        integer level
        integer index
        string left_child
        string right_child
        string snapshot_id FK
    }

    inputs ||--o{ predictions : "1:N"
    models ||--o{ predictions : "1:N"
    scheduled_prediction_configs ||--o{ predictions : "1:N"
    predictions ||--o| scores : "1:1"
    models ||--o{ snapshots : "1:N"
    checkpoints ||--o{ merkle_cycles : "1:N"
    merkle_cycles ||--o{ merkle_nodes : "1:N"
    snapshots ||--o| merkle_nodes : "leaf"
```

## Tables

### `feed_records`
Raw data from external feeds. Immutable append-only log with four dimensions: source, subject, kind, granularity.

### `inputs`
Dumb log of what was sent to models. `raw_data_jsonb` matches `RawInput`. Saved once, never updated.

### `models`
Registered model containers. `overall_score_jsonb` and `scores_by_scope_jsonb` are denormalized from leaderboard.

### `scheduled_prediction_configs`
Seeded from `CrunchConfig.scheduled_predictions` at init. Defines scope, schedule, and ordering for each prediction type.

### `predictions`
Core pipeline record. Each prediction carries:
- `scope_key` + `scope_jsonb` — what was predicted
- `resolvable_at` — when ground truth can be resolved
- `inference_output_jsonb` — the model's raw output (matches `InferenceOutput`)
- `status` — lifecycle: `PENDING → SCORED | FAILED | ABSENT`

### `scores`
One score per prediction. `result_jsonb` matches `ScoreResult`. Failed scores have `success=false` and `failed_reason`.

### `snapshots`
Per-model period summary. `result_summary_jsonb` is the output of `aggregate_snapshot()` — averages of all numeric score fields over the period. `content_hash` enables Merkle inclusion proofs.

### `leaderboards`
Point-in-time leaderboard. `entries_jsonb` contains ranked entries with windowed metrics. Rebuilt after each score cycle.

### `checkpoints`
On-chain emission records. Status lifecycle: `PENDING → SUBMITTED → CLAIMABLE → PAID`. Contains `merkle_root` for tamper evidence and `tx_hash` after submission.

### `merkle_cycles` / `merkle_nodes`
Tamper evidence. Each score cycle produces a mini Merkle tree over its snapshots. Cycles are chained (each stores `prev_cycle_root`). At checkpoint time, a tree over cycle roots produces the checkpoint's `merkle_root`.

## JSONB Column Mapping

| Column | Pydantic Type | Set By |
|--------|--------------|--------|
| `inputs.raw_data_jsonb` | `RawInput` | feed-data-worker |
| `predictions.scope_jsonb` | `PredictionScope` | predict-worker |
| `predictions.inference_output_jsonb` | `InferenceOutput` | predict-worker |
| `scores.result_jsonb` | `ScoreResult` | score-worker |
| `snapshots.result_summary_jsonb` | `dict` (from `aggregate_snapshot`) | score-worker |
| `leaderboards.entries_jsonb` | `list[dict]` (ranked entries) | score-worker |
| `checkpoints.entries_jsonb` | `list[dict]` (emission entries) | checkpoint-worker |
