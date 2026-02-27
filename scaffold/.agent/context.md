# Architecture

## Pipeline

```
Feed → Input → Prediction → Score → Snapshot → Leaderboard → Checkpoint
```

## Workers

| Worker | Role |
|---|---|
| `feed-data-worker` | Ingests feed data (Pyth, Binance) via polling |
| `predict-worker` | Event-driven (pg NOTIFY): feed → models → predictions |
| `score-worker` | Resolves ground truth → scores → snapshots → leaderboard → checkpoints |
| `report-worker` | FastAPI server for all report endpoints |

## CrunchConfig (`node/config/crunch_config.py`)

Single source of truth for competition behavior:

| Field | Purpose |
|---|---|
| `raw_input_type` | Feed data shape |
| `input_type` | What models receive (can transform from raw) |
| `output_type` | What models return |
| `ground_truth_type` | Actual outcome shape |
| `score_type` | What scoring produces |
| `scheduled_predictions` | Scope, interval, horizon per prediction type |
| `scoring_function` | Overrides `SCORING_FUNCTION` env var. Supports stateful callables. |
| `resolve_ground_truth` | Derives actuals from feed data |
| `aggregate_snapshot` | Aggregates scores into period summaries |
| `build_emission` | Reward distribution logic |
| `aggregation.value_field` | Score field to average in windows (default `"value"`) |
| `aggregation.ranking_key` | Metric that ranks the leaderboard |

Loaded via `coordinator_node.config_loader.load_config()`.

## Scoring → Leaderboard Flow

1. `scoring_function(prediction, ground_truth)` → dict matching `score_type`
2. `aggregate_snapshot([results])` → `SnapshotRecord.result_summary`
3. Window aggregation → averages `value_field` per window → leaderboard `metrics`
4. `auto_report_schema()` → introspects `score_type` → auto-generates UI columns

## Status Lifecycles

```
Input:       Saved once, never updated (id, raw_data, received_at)
Prediction:  PENDING → SCORED / FAILED / ABSENT (owns scope + resolvable_at)
Checkpoint:  PENDING → SUBMITTED → CLAIMABLE → PAID
```

## Report API

Full schema at `/openapi.json`. Key endpoints:

| Endpoint | Returns |
|---|---|
| `/healthz` | `{"status": "ok"}` |
| `/info` | Node identity (crunch_id, address, network) |
| `/reports/schema` | Auto-generated report schema + leaderboard columns |
| `/reports/models` | Registered models |
| `/reports/leaderboard` | Current rankings |
| `/reports/models/global` | Per-model windowed scores |
| `/reports/models/params` | Scores grouped by scope |
| `/reports/models/metrics` | Metrics timeseries |
| `/reports/models/summary` | Latest snapshot per model |
| `/reports/predictions` | Prediction history |
| `/reports/feeds` | Active feed subscriptions |
| `/reports/feeds/tail` | Latest feed records |
| `/reports/snapshots` | Per-model period summaries |
| `/reports/checkpoints` | Checkpoint history + emission payloads + prizes |
| `/reports/emissions/latest` | Latest emission data |
| `/reports/diversity` | Model diversity overview |
| `/reports/ensemble/history` | Ensemble performance over time |
| `/reports/merkle/*` | Merkle cycles, proofs, tamper evidence |
| `/reports/backfill/*` | Backfill job management |
| `/data/backfill/*` | Backfill parquet data (used by BacktestClient) |

## Feed Dimensions

Four dimensions configured in `node/.local.env`: `FEED_SOURCE`, `FEED_SUBJECTS`, `FEED_KIND`, `FEED_GRANULARITY`.

## Gotchas

### resolve_horizon_seconds must exceed feed interval
- `0` = immediate resolution (ground truth from `InputRecord.raw_data`)
- `> 0` = deferred. **Must exceed feed data interval** — otherwise fetch_window returns zero records and predictions silently fail to score.

### NEXT_PUBLIC_API_URL must use Docker DNS
Next.js rewrites run server-side inside Docker. `localhost` = the container itself.
- ✅ `http://report-worker:8000`
- ❌ `http://localhost:8000`

### InferenceOutput key mismatches are caught at startup
The score worker dry-runs the scoring function against default `InferenceOutput` and `GroundTruth` values on startup. A `KeyError` (scoring reads a field not in `InferenceOutput`) raises a hard `RuntimeError`. The predict worker also validates every model output against `InferenceOutput` and logs `INFERENCE_OUTPUT_VALIDATION_ERROR` if no keys match.

### score_prediction receives injected fields
Score worker adds `model_id` and `prediction_id` to the output dict before calling the scoring function.

### All scores zero?
Scoring stub not replaced, or `resolve_horizon_seconds` ≤ feed interval, or `resolve_ground_truth` returns zeroed data.

### Leaderboard rankings all zero?
`aggregation.value_field` doesn't match any field in `score_type`.

### Ports in use
```bash
lsof -nP -iTCP:<port> -sTCP:LISTEN
```

### BAD_IMPLEMENTATION in model logs
Check `MODEL_BASE_CLASSNAME=tracker.TrackerBase` in `node/.local.env`.

### Clean reset
```bash
make down && rm -rf .venv && make deploy && make verify-e2e
```
