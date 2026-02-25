# Project Context — coordinator-node-starter

## What this is

A Crunch coordinator workspace running a competition pipeline. Two packages in one workspace:

- `node/` — competition infrastructure (docker-compose, workers, config, API)
- `challenge/` — participant-facing package (tracker interface, scoring, backtest, examples)

The node runs `coordinator-node` (published to PyPI) as its engine.

## Architecture

### Pipeline

```
Feed → Input → Prediction → Score → Snapshot → Leaderboard → Checkpoint → On-chain
```

### Workers

| Worker | Purpose |
|---|---|
| `feed-data-worker` | Ingests feed data (Pyth, Binance, etc.) via polling + backfill |
| `predict-worker` | Event-driven: feed → models → predictions |
| `score-worker` | Resolves actuals → scores → snapshots → leaderboard |
| `checkpoint-worker` | Aggregates snapshots → EmissionCheckpoint |
| `report-worker` | FastAPI server: leaderboard, predictions, feeds, snapshots, checkpoints |

### CrunchConfig

All type shapes and behavior defined in `node/config/crunch_config.py`:

```python
class CrunchConfig(BaseModel):
    # Type-safe JSONB boundaries
    raw_input_type: type[BaseModel] = RawInput         # feed data shape
    ground_truth_type: type[BaseModel] = GroundTruth   # actual outcome shape
    input_type: type[BaseModel] = InferenceInput       # what models receive
    output_type: type[BaseModel] = InferenceOutput     # what models return
    score_type: type[BaseModel] = ScoreResult          # per-prediction score shape

    # Prediction context
    scope: PredictionScope = PredictionScope()
    call_method: CallMethodConfig = CallMethodConfig()

    # Aggregation
    aggregation: Aggregation = Aggregation()
    # aggregation.value_field = "value"     # score field to average in windows
    # aggregation.ranking_key = "score_recent"  # which metric to rank by

    # Scheduled predictions (replaces scheduled_prediction_configs.json)
    scheduled_predictions: list[ScheduledPrediction] = [...]

    # Multi-metric scoring (set metrics=[] to disable)
    metrics: list[str] = ["ic", "ic_sharpe", ...]
    compute_metrics: Callable = default_compute_metrics

    # Ensembles (default: off)
    ensembles: list[EnsembleConfig] = []

    # Callables
    scoring_function: Callable | None = None  # if set, takes precedence over env var
    resolve_ground_truth: Callable = default_resolve_ground_truth
    aggregate_snapshot: Callable = default_aggregate_snapshot
    build_emission: Callable = default_build_emission
```

### Scoring → Snapshots → Leaderboard data flow

1. `scoring_function(prediction, ground_truth)` → dict matching `score_type`
2. `score_type.model_validate(result)` → `ScoreRecord.result` (JSONB)
3. `aggregate_snapshot([results...])` → averages all numeric fields → `SnapshotRecord.result_summary`
4. `_aggregate_from_snapshots()` → reads `value_field` from snapshots for windows, merges latest snapshot numeric fields → leaderboard `metrics` dict
5. `auto_report_schema()` → introspects `score_type.model_fields` → auto-generates leaderboard columns

### Status Lifecycles

```
Input:       Saved once, never updated (dumb log)
Prediction:  PENDING → SCORED / FAILED / ABSENT
Checkpoint:  PENDING → SUBMITTED → CLAIMABLE → PAID
```

### Feed Dimensions

Four generic dimensions: **source**, **subject**, **kind**, **granularity**.
Env vars: `FEED_SOURCE`, `FEED_SUBJECTS`, `FEED_KIND`, `FEED_GRANULARITY`.

### Naming Conventions

- `resolve_horizon_seconds` (not `resolve_after_seconds` or `horizon_seconds`)
- `prediction_interval_seconds` (not `step_seconds` for scheduling)
- `step_seconds` is the feed granularity hint passed to models
- Config loaded via `config_loader.load_config()` (not contracts.py)

---

## Quick reference

### From workspace root

```bash
make deploy       # Build and start all services
make verify       # API + container checks
make verify-ui    # Browser-based UI checks
make verify-all   # Both
make logs         # Stream service logs
make down         # Tear down
make test         # Unit tests (no Docker)
```

### Where to edit code

| What to change | Where to edit |
|---|---|
| Challenge behavior (tracker, scoring, examples) | `challenge/starter_challenge/` |
| Competition config (types, callables, schedules) | `node/config/crunch_config.py` |
| Node config (env, deployment) | `node/` (.local.env, deployment/) |
| Custom API endpoints | `node/api/` |

### Where to put new code

| I want to… | Put it in |
|---|---|
| Add a new API endpoint | `node/api/` — `.py` file with `router = APIRouter()`, auto-mounted |
| Override scoring/aggregation/emission | `node/config/crunch_config.py` — override callable fields |
| Add node-side extensions | `node/extensions/` — position manager, fee engine, etc. |
| Change the model interface | `challenge/starter_challenge/tracker.py` |
| Add quickstarter examples | `challenge/starter_challenge/examples/` |

## Tests

Two test suites:

### Unit tests (`tests/`)

```bash
make test   # from repo root
```

Tests CrunchConfig wiring, scoring pipeline, report endpoints, repositories.
PYTHONPATH includes `base/challenge:base/node`.

### Deployment verification

```bash
make deploy
make verify-all   # API checks + browser UI checks
```

Checks containers, all API endpoints, data pipeline flow, docker logs, UI pages.
