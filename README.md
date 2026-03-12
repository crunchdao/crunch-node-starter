# crunch-node

[![PyPI](https://img.shields.io/pypi/v/crunch-node)](https://pypi.org/project/crunch-node/)

Runtime engine for Crunch nodes. Powers the full competition pipeline — from data ingestion through scoring to on-chain emission checkpoints.

```bash
pip install crunch-node
```

---

## Two ways to use this repo

### 1. Scaffold a new competition (recommended)

Use the Crunch CLI to create a self-contained workspace that pulls `crunch-node` from PyPI:

```bash
crunch-cli init-workspace my-challenge
cd my-challenge
make deploy
```

This creates:

```
my-challenge/
├── node/          ← docker-compose, config, scripts (uses crunch-node from PyPI)
├── challenge/     ← participant-facing package (tracker, scoring, examples)
└── Makefile
```

### 2. Develop the engine itself

Clone this repo to work on the `crunch_node` package directly:

```bash
git clone https://github.com/crunchdao/coordinator-node-starter.git
cd crunch-node-starter
uv sync
make deploy    # uses local crunch_node/ via COPY in Dockerfile
```

Changes to `crunch_node/` are picked up immediately on rebuild.

---

## Architecture

### Pipeline

```
Feed → Input → Prediction → Score → Snapshot → Checkpoint → On-chain
```

### Predict latency target (architecture SLO)

The architecture should support **~50ms predict roundtrip** when optimized.

- Definition: predict-worker roundtrip from new data wake-up/availability to prediction persistence.
- Any architecture decision expected to push this materially above ~50ms **must be called out explicitly** in specs, PR notes, and agent output.
- If such a deviation is required, include rationale, expected impact, and mitigation options.

### Architecture docs

Detailed C4 + refactor documentation:

- `docs/architecture/README.md`
- `docs/architecture/predict-service-kernel-architecture.md`

### Workers

| Worker | Purpose |
|---|---|
| `predict-worker` | Ingests feed data (Pyth, Binance, etc.), ticks models, collects predictions |
| `score-worker` | Resolves actuals → scores predictions → writes snapshots → rebuilds leaderboard → creates checkpoints |
| `report-worker` | FastAPI server: leaderboard, predictions, feeds, snapshots, checkpoints |

### Feed Dimensions

| Dimension | Example | Env var |
|---|---|---|
| `source` | pyth, binance | `FEED_SOURCE` |
| `subject` | BTC, ETH | `FEED_SUBJECTS` |
| `kind` | tick, candle | `FEED_KIND` |
| `granularity` | 1s, 1m | `FEED_GRANULARITY` |

### Status Lifecycles

```
Input:       RECEIVED → RESOLVED
Prediction:  PENDING → SCORED / FAILED / ABSENT
Checkpoint:  PENDING → SUBMITTED → CLAIMABLE → PAID
```

---

## Configuration

All configuration is via environment variables. Copy the example env file to get started:

```bash
cp .local.env.example .local.env
```

Key variables:

| Variable | Description | Default |
|---|---|---|
| `CRUNCH_ID` | Competition identifier | `starter-challenge` |
| `FEED_SOURCE` | Data source | `pyth` |
| `FEED_SUBJECTS` | Assets to track | `BTC` |
| `SCORING_FUNCTION` | Dotted path to scoring callable | `crunch_node.extensions.default_callables:default_score_prediction` |
| `CHECKPOINT_INTERVAL_SECONDS` | Seconds between checkpoints | `604800` |
| `MODEL_BASE_CLASSNAME` | Participant model base class | `cruncher.BaseModelClass` |
| `MODEL_RUNNER_NODE_HOST` | Model orchestrator host | `model-orchestrator` |

---

## API Security

Endpoints are protected by API key authentication when `API_KEY` is set. Off by default for backward compatibility.

### Quick start

```bash
# In .local.env
API_KEY=my-strong-secret
```

After `make deploy`, admin endpoints require the key:

```bash
# Rejected (401)
curl -s http://localhost:8000/reports/backfill

# Accepted
curl -s -H "X-API-Key: my-strong-secret" http://localhost:8000/reports/backfill
```

### Endpoint tiers

| Tier | Default prefixes | Auth required |
|------|-----------------|---------------|
| **Public** | `/healthz`, `/reports/leaderboard`, `/reports/schema`, `/reports/models`, `/reports/feeds`, `/info`, `/docs` | Never |
| **Read** | `/reports/predictions`, `/reports/snapshots`, `/data/backfill/*` | Only if `API_READ_AUTH=true` |
| **Admin** | `/reports/backfill` (POST), `/reports/checkpoints/`, `/custom/*` | Always (when API_KEY set) |

### Sending the key

Three methods (any one works):

```bash
# X-API-Key header (recommended)
curl -H "X-API-Key: <key>" ...

# Authorization: Bearer header
curl -H "Authorization: Bearer <key>" ...

# Query parameter (for quick testing only)
curl "...?api_key=<key>"
```

### Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `API_KEY` | _(empty)_ | Shared secret. When unset, all endpoints are open. |
| `API_READ_AUTH` | `false` | When `true`, read endpoints also require the API key. |
| `API_PUBLIC_PREFIXES` | See above | Comma-separated prefixes that never require auth. |
| `API_ADMIN_PREFIXES` | See above | Comma-separated prefixes that always require auth. |

### Custom `api/` endpoints

Custom endpoints under `/custom/` are **admin-tier by default** — they require the API key when set. To make a custom endpoint public, add its prefix to `API_PUBLIC_PREFIXES`.

---

## Custom API Endpoints

Add endpoints to the report worker by dropping Python files in `node/api/`:

```python
# node/api/my_endpoints.py
from fastapi import APIRouter

router = APIRouter(prefix="/custom", tags=["custom"])

@router.get("/hello")
def hello():
    return {"message": "Hello from custom endpoint"}
```

After `make deploy`, available at `http://localhost:8000/custom/hello`.

Any `.py` file in `api/` with a `router` attribute (a FastAPI `APIRouter`) is auto-mounted at startup. Files starting with `_` are skipped.

Full database access is available via the same dependency injection pattern:

```python
from typing import Annotated
from fastapi import APIRouter, Depends
from sqlmodel import Session
from crunch_node.db import create_session, DBModelRepository

router = APIRouter(prefix="/custom")

def get_db_session():
    with create_session() as session:
        yield session

@router.get("/models/count")
def model_count(session: Annotated[Session, Depends(get_db_session)]):
    return {"count": len(DBModelRepository(session).fetch_all())}
```

| Env var | Default | Description |
|---------|---------|-------------|
| `API_ROUTES_DIR` | `api/` | Directory to scan for router files |
| `API_ROUTES` | _(empty)_ | Comma-separated `module:attr` paths for explicit imports |

---

## Extension Points

Customize competition behavior by setting callable paths in your env:

| Env var | Purpose |
|---|---|
| `SCORING_FUNCTION` | Score a prediction against ground truth |
| `INFERENCE_INPUT_BUILDER` | Transform raw feed data into model input |
| `INFERENCE_OUTPUT_VALIDATOR` | Validate model output shape/values |
| `MODEL_SCORE_AGGREGATOR` | Aggregate per-model scores across predictions |
| `LEADERBOARD_RANKER` | Custom leaderboard ranking strategy |

---

## CrunchConfig

All type shapes and behavior are defined in a single `CrunchConfig`. Workers auto-discover the operator's config at startup:

```python
from crunch_node.crunch_config import CrunchConfig, EnsembleConfig

contract = CrunchConfig(
    # Type shapes
    raw_input_type=RawInput,
    output_type=InferenceOutput,
    score_type=ScoreResult,
    scope=PredictionScope(),
    aggregation=Aggregation(),

    # Multi-metric scoring (default: 5 active metrics)
    metrics=["ic", "ic_sharpe", "hit_rate", "max_drawdown", "model_correlation"],

    # Ensemble (default: off)
    ensembles=[],

    # Callables
    resolve_ground_truth=default_resolve_ground_truth,
    aggregate_snapshot=default_aggregate_snapshot,
    build_emission=default_build_emission,
)
```

### Config resolution order

All workers use `load_config()` which tries, in order:

1. `CRUNCH_CONFIG_MODULE` env var (e.g. `my_package.crunch_config:MyCrunchConfig`)
2. `config.crunch_config:CrunchConfig` — the standard operator override
3. `crunch_node.crunch_config:CrunchConfig` — engine default

The operator's config is imported automatically — no env var needed if `config/crunch_config.py` exists on `PYTHONPATH` (it does in the Docker setup).

---

## Multi-Metric Scoring

Every score cycle computes portfolio-level metrics alongside the per-prediction scoring function. Metrics are stored in snapshot `result_summary` JSONB and surfaced on the leaderboard.

### Active metrics

Set in the contract — only listed metrics are computed:

```python
# Use all defaults (ic, ic_sharpe, hit_rate, max_drawdown, model_correlation)
contract = CrunchConfig()

# Opt out entirely — per-prediction scoring only
contract = CrunchConfig(metrics=[])

# Pick specific metrics
contract = CrunchConfig(metrics=["ic", "sortino_ratio", "turnover"])
```

### Built-in metrics

| Tier | Name | Description |
|------|------|-------------|
| T1 | `ic` | Information Coefficient — Spearman rank correlation vs. actual returns |
| T1 | `ic_sharpe` | mean(IC) / std(IC) — rewards consistency |
| T1 | `mean_return` | Mean return of a long-short portfolio from signals |
| T1 | `hit_rate` | % of predictions with correct directional sign |
| T1 | `model_correlation` | Mean pairwise correlation against other models |
| T2 | `max_drawdown` | Worst peak-to-trough on cumulative score |
| T2 | `sortino_ratio` | Sharpe but only penalizes downside |
| T2 | `turnover` | Signal change rate between consecutive predictions |
| T3 | `fnc` | Feature-Neutral Correlation (ensemble-aware) |
| T3 | `contribution` | Leave-one-out ensemble contribution |
| T3 | `ensemble_correlation` | Correlation to ensemble output |

T3 metrics require ensembling to be enabled.

### Custom metrics

Register your own metric function:

```python
from crunch_node.metrics import get_default_registry

def my_custom_metric(predictions, scores, context):
    """Return a single float."""
    return some_computation(predictions, scores)

get_default_registry().register("my_custom", my_custom_metric)

# Then add it to the contract
contract = CrunchConfig(metrics=["ic", "my_custom"])
```

### Ranking by any metric

The leaderboard can rank by any active metric. Set `ranking_key` to a metric name:

```python
contract = CrunchConfig(
    metrics=["ic", "ic_sharpe", "hit_rate"],
    aggregation=Aggregation(ranking_key="ic_sharpe"),
)
```

---

## Ensemble Framework

Combine multiple model predictions into virtual meta-models. Off by default — opt in via the contract.

### Quick start

```python
from crunch_node.crunch_config import CrunchConfig, EnsembleConfig
from crunch_node.services.ensemble import inverse_variance, equal_weight, top_n

contract = CrunchConfig(
    ensembles=[
        EnsembleConfig(name="main", strategy=inverse_variance),
        EnsembleConfig(name="top5", strategy=inverse_variance, model_filter=top_n(5)),
        EnsembleConfig(name="equal", strategy=equal_weight),
    ],
)
```

### How it works

1. After scoring, the score worker computes ensembles for each enabled config
2. Models are filtered (optional `model_filter`)
3. Weights computed via `strategy(model_metrics, predictions) → {model_id: weight}`
4. Weighted-average predictions stored as `PredictionRecord` with `model_id="__ensemble_{name}__"`
5. Virtual models are scored, metrics computed, and appear in leaderboard data

### Built-in strategies

| Strategy | Description |
|----------|-------------|
| `inverse_variance` | Weight = 1/var(predictions), normalized. Default. |
| `equal_weight` | 1/N for all included models. |

### Model filters

```python
from crunch_node.services.ensemble import top_n, min_metric

# Keep only top 5 by score
EnsembleConfig(name="top5", model_filter=top_n(5))

# Keep models with IC > 0.03
EnsembleConfig(name="quality", model_filter=min_metric("ic", 0.03))
```

### Leaderboard filtering

Ensemble virtual models are hidden from the leaderboard by default. Toggle with:

```
GET /reports/leaderboard?include_ensembles=true
GET /reports/models/global?include_ensembles=true
GET /reports/models/params?include_ensembles=true
```

### Contribution-aware rewards

The default `build_emission` uses tier-based ranking. For competitions that want to incentivize diversity, switch to `contribution_weighted_emission`:

```python
from crunch_node.extensions.emission_strategies import contribution_weighted_emission

config = CrunchConfig(
    build_emission=contribution_weighted_emission,
    metrics=["ic", "ic_sharpe", "hit_rate", "model_correlation", "contribution"],
    ensembles=[EnsembleConfig(name="main")],
)
```

This blends three factors into reward allocation:
- **Rank** (50%): inverse rank — higher-ranked models get more
- **Contribution** (30%): ensemble contribution — models that improve the ensemble get more
- **Diversity** (20%): 1 - model_correlation — unique signals get more

Weights are configurable: `contribution_weighted_emission(..., rank_weight=0.3, contribution_weight=0.5, diversity_weight=0.2)`.

### Ensemble signal endpoint

Activate the built-in ensemble signal API by renaming:

```bash
mv node/api/ensemble_signals.py.disabled node/api/ensemble_signals.py
make deploy
```

Endpoints:
```
GET /signals/ensemble              → list available ensembles
GET /signals/ensemble/{name}       → latest ensemble prediction (the product)
GET /signals/ensemble/{name}/history → recent prediction history
```

### Diversity feedback for competitors

Competitors can see how their model relates to the collective:

```
GET /reports/models/{model_id}/diversity
```

Returns:
```json
{
  "model_id": "my_model",
  "rank": 3,
  "diversity_score": 0.75,
  "metrics": {
    "ic": 0.035,
    "model_correlation": 0.25,
    "ensemble_correlation": 0.60,
    "contribution": 0.02,
    "fnc": 0.03
  },
  "guidance": [
    "Low correlation + positive IC — your model provides unique alpha."
  ]
}
```

The backtest harness also surfaces diversity metrics:
```python
result = BacktestRunner(model=MyTracker()).run(...)
result.summary()  # includes diversity section when model_id is set
result.diversity  # fetches live diversity feedback from coordinator
```

---

## Report API

| Endpoint | Params | Description |
|---|---|---|
| `GET /reports/leaderboard` | `include_ensembles` (bool, default false) | Current leaderboard |
| `GET /reports/models` | | Registered models |
| `GET /reports/models/global` | `projectIds`, `start`, `end`, `include_ensembles` | Global model scores |
| `GET /reports/models/params` | `projectIds`, `start`, `end`, `include_ensembles` | Per-scope model scores |
| `GET /reports/predictions` | `projectIds`, `start`, `end` | Prediction history |
| `GET /reports/feeds` | | Active feed subscriptions |
| `GET /reports/models/{id}/diversity` | | Diversity feedback: correlation, contribution, guidance |
| `GET /reports/diversity` | `limit` | All models' diversity scores for dashboard chart |
| `GET /reports/ensemble/history` | `ensemble_name`, `since`, `until`, `limit` | Ensemble metrics over time |
| `GET /reports/checkpoints/rewards` | `model_id`, `limit` | Reward distribution per checkpoint |
| `GET /reports/snapshots` | `model_id`, `since`, `until`, `limit` | Per-model period summaries (enriched with metrics) |
| `GET /reports/checkpoints` | `status`, `limit` | Checkpoint history |
| `GET /reports/checkpoints/{id}/emission` | | Raw emission (frac64) |
| `GET /reports/checkpoints/{id}/emission/cli-format` | | CLI JSON format |
| `GET /reports/emissions/latest` | | Latest emission |
| `POST /reports/checkpoints/{id}/confirm` | `tx_hash` | Record tx_hash |
| `PATCH /reports/checkpoints/{id}/status` | `status` | Advance status |

### Backfill & Data

| Endpoint | Description |
|---|---|
| `GET /reports/backfill/feeds` | Configured feeds eligible for backfill |
| `POST /reports/backfill` | Start a backfill job (409 if one running) |
| `GET /reports/backfill/jobs` | List all backfill jobs |
| `GET /reports/backfill/jobs/{id}` | Job detail with progress percentage |
| `GET /data/backfill/index` | Manifest of available parquet files |
| `GET /data/backfill/{source}/{subject}/{kind}/{granularity}/{file}` | Download parquet file |

---

## Backfill & Backtest

### Coordinator-side backfill

Backfill historical data from the UI or API:

1. Admin triggers backfill via `POST /reports/backfill` (or the UI)
2. Data is fetched from the configured feed provider (Binance, Pyth, etc.)
3. Written to Hive-partitioned parquet files: `data/backfill/{source}/{subject}/{kind}/{granularity}/YYYY-MM-DD.parquet`
4. Progress tracked in `backfill_jobs` table (resumable on restart)
5. Parquet files served via `/data/backfill/` endpoints for model consumption

### Competitor-side backtest

The challenge package includes a backtest harness. Competitors run backtests locally — model code is identical to production:

```python
from starter_challenge.backtest import BacktestRunner
from my_model import MyTracker

result = BacktestRunner(model=MyTracker()).run(
    start="2026-01-01", end="2026-02-01"
)
result.predictions_df   # DataFrame in notebook
result.metrics           # rolling windows + multi-metric enrichment
result.summary()         # formatted output

# result.metrics example:
# {
#   'score_recent': 0.42, 'score_steady': 0.38, 'score_anchor': 0.35,
#   'ic': 0.035, 'ic_sharpe': 1.2, 'hit_rate': 0.58,
#   'mean_return': 0.012, 'max_drawdown': -0.08, 'sortino_ratio': 1.5,
#   'turnover': 0.23,
# }
```

- Data auto-fetched from coordinator and cached locally on first run
- Coordinator URL and feed dimensions baked into challenge package
- Same `tick()` → `predict()` loop as production
- Same scoring function, rolling window metrics, and multi-metric evaluation as leaderboard

---

## Emission Checkpoints

Checkpoints produce `EmissionCheckpoint` matching the on-chain protocol:

```json
{
    "crunch": "<pubkey>",
    "cruncher_rewards": [{"cruncher_index": 0, "reward_pct": 350000000}],
    "compute_provider_rewards": [],
    "data_provider_rewards": []
}
```

`reward_pct` uses frac64 (1,000,000,000 = 100%).

---

## Database Tables

### Feed layer

| Table | Purpose |
|---|---|
| `feed_records` | Raw data points from external sources. Keyed by `(source, subject, kind, granularity, ts_event)`. Values and metadata stored as JSONB. |
| `feed_ingestion_state` | Tracks the last ingested timestamp per feed scope to enable incremental polling and backfill. |

### Backfill layer

| Table | Purpose |
|---|---|
| `backfill_jobs` | Tracks backfill runs. Status: `pending → running → completed / failed`. Stores cursor for resume, records written, pages fetched. |

Historical backfill data is stored as Hive-partitioned parquet files at `data/backfill/{source}/{subject}/{kind}/{granularity}/YYYY-MM-DD.parquet` (not in Postgres).

### Pipeline layer

| Table | Purpose |
|---|---|
| `inputs` | Incoming data events. Status: `RECEIVED → RESOLVED`. Holds raw data, actuals (once known), and scope metadata. |
| `predictions` | One row per model per input. Links to a `scheduled_prediction_config`. Stores inference output, execution time, and resolution timestamp. Status: `PENDING → SCORED / FAILED / ABSENT`. |
| `scores` | One row per scored prediction. Stores the result payload, success flag, and optional failure reason. |
| `snapshots` | Per-model period summaries. Aggregates prediction counts and result metrics over a time window. |
| `checkpoints` | Periodic emission checkpoints. Aggregates snapshots into on-chain reward distributions. Status: `PENDING → SUBMITTED → CLAIMABLE → PAID`. |
| `scheduled_prediction_configs` | Defines when and what to predict — scope template, schedule, and ordering. Seeded at init from `CrunchConfig.scheduled_predictions`. |

### Model layer

| Table | Purpose |
|---|---|
| `models` | Registered participant models. Tracks overall and per-scope scores as JSONB. |
| `leaderboards` | Point-in-time leaderboard snapshots with ranked entries as JSONB. |

---

## Local Development

```bash
# Run tests
uv run pytest tests/ -x -q

# Start all services locally
make deploy

# View logs
make logs

# Tear down
make down
```

---

## Project Structure

```
crunch-node-starter/
├── crunch_node/       ← core engine (published to PyPI as crunch-node)
│   ├── workers/            ← feed, predict, score, checkpoint, report workers
│   ├── services/           ← business logic
│   ├── entities/           ← domain models
│   ├── db/                 ← database tables and init
│   ├── feeds/              ← data source adapters (Pyth, Binance, etc.)
│   ├── schemas/            ← API schemas
│   ├── extensions/         ← default callables
│   ├── config/             ← runtime configuration
│   └── crunch_config.py    ← base CrunchConfig class and default types
├── scaffold/               ← template used by crunch-cli init-workspace
│   ├── node/               ← node template (Dockerfile, docker-compose, config)
│   └── challenge/          ← challenge template (tracker, scoring, backtest, examples)
├── tests/                  ← test suite
├── docker-compose.yml      ← local dev compose (uses local crunch_node/)
├── Dockerfile              ← local dev Dockerfile (COPYs crunch_node/)
├── pyproject.toml          ← package definition
└── Makefile                ← deploy / down / logs / test
```

---

## Publishing

```bash
uv build
twine upload dist/*
```
