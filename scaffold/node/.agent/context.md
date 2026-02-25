# Node Context — coordinator-node-starter

## What this is

Node runtime workspace. Contains docker-compose, workers, config, and the report API. Runs the `coordinator-node` engine from PyPI.

## Primary commands

```bash
make deploy       # Build and start all services
make verify       # API + container checks (headless)
make verify-ui    # Browser-based UI page checks
make verify-all   # Both
make logs         # Stream all service logs
make down         # Tear down
```

## Workers

| Container | Purpose |
|---|---|
| `feed-data-worker` | Ingests feed data (Pyth, Binance) |
| `predict-worker` | Event-driven: feed → models → predictions |
| `score-worker` | Resolves actuals → scores → snapshots → leaderboard |
| `checkpoint-worker` | Aggregates snapshots → EmissionCheckpoint |
| `report-worker` | FastAPI serving all report endpoints |

## Report API

| Endpoint | Description |
|---|---|
| `GET /healthz` | Health check |
| `GET /info` | Node identity (crunch_id, address, network) |
| `GET /reports/schema` | Auto-generated report schema |
| `GET /reports/models` | Registered models |
| `GET /reports/leaderboard` | Current leaderboard |
| `GET /reports/models/global` | Per-model windowed scores |
| `GET /reports/models/params` | Scores grouped by scope |
| `GET /reports/models/metrics` | Metrics timeseries |
| `GET /reports/models/summary` | Latest snapshot per model |
| `GET /reports/predictions` | Prediction history |
| `GET /reports/feeds` | Active feed subscriptions |
| `GET /reports/feeds/tail` | Latest feed records |
| `GET /reports/snapshots` | Per-model period summaries |
| `GET /reports/checkpoints` | Checkpoint history |
| `GET /reports/diversity` | Model diversity overview |
| `GET /reports/ensemble/history` | Ensemble performance over time |
| `GET /reports/merkle/cycles` | Merkle tamper evidence |

## Folder map

| Folder | Purpose |
|---|---|
| `config/` | `crunch_config.py` — all type shapes, callables, scheduled predictions |
| `api/` | Custom FastAPI endpoints (auto-discovered, drop `.py` with `router`) |
| `extensions/` | Node-side extensions (position manager, fee engine, etc.) |
| `deployment/` | Local deployment assets (model-orchestrator, report-ui) |
| `scripts/` | Utility scripts (`verify_deployment.sh`, etc.) |

## Edit boundaries

| What | Where |
|---|---|
| Competition types & behavior | `config/crunch_config.py` |
| Node env config | `.local.env` |
| Prediction schedules | `config/crunch_config.py` → `scheduled_predictions` |
| Custom API endpoints | `api/` |
| Node-side extensions | `extensions/` |
| Local deployment config | `deployment/` |

## Key constraints

### resolve_horizon_seconds
- `0` = immediate resolution (live trading). Ground truth from `InputRecord.raw_data`.
- `> 0` = deferred. Must exceed feed data interval, otherwise no feed records exist for scoring.

### Aggregation
- `value_field` = score field to average in windows (default `"value"`)
- `ranking_key` = which metric to rank by (can be window name or score field)
- Windows average `value_field` over their time range
- Latest snapshot's numeric fields auto-merged into leaderboard

### CrunchConfig.scoring_function
- If set, takes precedence over `SCORING_FUNCTION` env var
- Enables stateful scoring (e.g. PositionManager-backed trading)

### Config loading
- `config_loader.load_config()` resolves: `CRUNCH_CONFIG_MODULE` env → `config.crunch_config:CrunchConfig` → engine default
- No `contracts.py`, no `contract_loader.py`, no `callables.env`

## ⛔ Known gotchas

### 1. NEXT_PUBLIC_API_URL must be Docker-internal
The UI's Next.js `rewrites()` proxy runs server-side inside Docker. Never set to `localhost`.
- ✅ `http://report-worker:8000` (Docker DNS)
- ❌ `http://localhost:8000` → ECONNREFUSED inside container

### 2. Input is a dumb log
`InputRecord` has only `id`, `raw_data`, `received_at`. No status, no actuals, no scope.
Saved once, never updated.

### 3. Predictions own their resolution
`PredictionRecord` carries `scope` (with feed dimensions) and `resolvable_at`.
Score worker queries by `status=PENDING, resolvable_before=now`.

### 4. score_prediction receives coerced output + model_id
The score worker injects `typed_output["model_id"]` and `typed_output["prediction_id"]`
before calling the scoring function. Use these for stateful per-model tracking.
