# Report API

The report worker exposes a FastAPI server with all competition data available via REST endpoints. The UI consumes these endpoints, and they're also useful for monitoring, debugging, and integration.

## Base URL

Default: `http://localhost:8000` (configurable via `REPORT_API_PORT`)

## Endpoints

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/healthz` | Health check — returns 200 if the service is running |
| `GET` | `/info` | Node identity: `crunch_id`, `crunch_address`, `network` |

### Schema

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reports/schema` | Auto-generated report schema — leaderboard columns, score fields, metric definitions. Introspected from `score_type` fields. |

### Models

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reports/models` | All registered model containers |
| `GET` | `/reports/models/global` | Per-model windowed scores (score_recent, score_steady, etc.) |
| `GET` | `/reports/models/params` | Scores grouped by prediction scope |
| `GET` | `/reports/models/metrics` | Metrics timeseries (IC, hit rate, etc.) |
| `GET` | `/reports/models/summary` | Latest snapshot per model |

### Leaderboard

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reports/leaderboard` | Current ranked leaderboard with windowed metrics |

### Predictions

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reports/predictions` | Prediction history with filters |

**Query parameters:**
- `projectIds` — comma-separated model IDs
- `start` / `end` — ISO datetime range
- `status` — filter by prediction status
- `limit` / `offset` — pagination

### Feeds

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reports/feeds` | Active feed subscriptions |
| `GET` | `/reports/feeds/tail` | Latest feed records (tail of the feed log) |

### Snapshots & Checkpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reports/snapshots` | Per-model period summaries |
| `GET` | `/reports/checkpoints` | Checkpoint history with status and Merkle roots |

### Checkpoint Management

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/reports/checkpoints/{id}/confirm` | Confirm checkpoint with tx_hash |
| `PATCH` | `/reports/checkpoints/{id}/status` | Update checkpoint status |

### Diversity & Ensembles

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reports/diversity` | Model diversity overview (pairwise correlations) |
| `GET` | `/reports/ensemble/history` | Ensemble performance over time |

### Merkle

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reports/merkle/cycles` | Merkle cycle history with roots and chain links |

## Authentication

When `API_KEY` env var is set, all endpoints require the header:
```
Authorization: Bearer <API_KEY>
```

The `/healthz` endpoint is always unauthenticated.

## CORS

All origins are allowed by default (configurable in the report worker).

## Custom Endpoints

The scaffold supports custom API endpoints via auto-discovery. Drop a `.py` file in `node/api/` with a `router = APIRouter()` and it's automatically mounted:

```python
# node/api/my_endpoints.py
from fastapi import APIRouter

router = APIRouter()

@router.get("/my-custom-data")
def get_custom_data():
    return {"hello": "world"}
```

The `api_discovery` module scans the `api/` directory at startup and mounts all routers found.
