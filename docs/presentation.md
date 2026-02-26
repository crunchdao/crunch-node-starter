---
marp: true
theme: default
paginate: true
backgroundColor: #0d1117
color: #c9d1d9
style: |
  section {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  }
  h1, h2 { color: #58a6ff; }
  h3 { color: #8b949e; }
  strong { color: #f0883e; }
  code { background: #161b22; color: #79c0ff; padding: 2px 6px; border-radius: 4px; }
  pre { background: #161b22 !important; border-radius: 8px; }
  a { color: #58a6ff; }
  table { font-size: 0.8em; }
  th { background: #161b22; color: #58a6ff; }
  td { background: #0d1117; }
  img[alt~="center"] { display: block; margin: 0 auto; }
  section.lead h1 { font-size: 2.5em; color: #f0883e; }
  section.lead h2 { font-size: 1.3em; color: #8b949e; font-weight: normal; }
  .columns { display: flex; gap: 2em; }
  .columns > div { flex: 1; }
  blockquote { border-left: 4px solid #f0883e; padding-left: 1em; color: #8b949e; }
---

<!-- _class: lead -->

# Coordinator Node

## A Runtime Engine for Decentralized Competitions

**CrunchDAO** — February 2026

---

# What Problem Are We Solving?

Running a **decentralized competition** requires:

- 📡 **Live data ingestion** from multiple sources
- 🧠 **Model orchestration** — calling many models in real-time
- 📊 **Fair scoring** — deterministic, auditable, tamper-proof
- 🏆 **Leaderboards** — rolling windows, multi-metric ranking
- ⛓️ **On-chain checkpoints** — binding reward distributions
- 🖥️ **Dashboard** — real-time visibility into everything

Building all this from scratch for each competition? **No.**

---

# The Solution: coordinator-node

A **single engine** that powers any competition.

Operators customize **one Python file** to define:
- What models predict
- How predictions are scored
- How performance is aggregated

The engine handles everything else.

```bash
pip install coordinator-node
```

```bash
crunch-cli init-workspace my-challenge
cd my-challenge
make deploy
```

---

# Architecture Overview

```
                    ┌──────────────────────────────────────┐
                    │          External World               │
                    │  Data Feeds    Models    Blockchain   │
                    └───┬──────────────┬───────────┬───────┘
                        │              │           │
                    ┌───▼───┐   ┌──────▼──┐  ┌────▼────┐
                    │ feed  │   │ predict │  │ check-  │
                    │ data  │   │ worker  │  │ point   │
                    │worker │   │         │  │ worker  │
                    └───┬───┘   └────┬────┘  └────┬────┘
                        │            │            │
                    ┌───▼────────────▼────────────▼───┐
                    │         PostgreSQL               │
                    └───┬────────────┬────────────┬───┘
                        │            │            │
                    ┌───▼───┐  ┌─────▼────┐  ┌───▼───┐
                    │ score │  │ report   │  │  UI   │
                    │worker │  │ worker   │  │       │
                    └───────┘  └──────────┘  └───────┘
```

**5 workers** communicate only through **PostgreSQL**. No Redis, no message queues.

---

# The Pipeline

Each piece of data flows through a clear, linear pipeline:

### Feed → Input → Prediction → Score → Snapshot → Leaderboard → Checkpoint

| Stage | What Happens |
|-------|-------------|
| **Feed** | Live data arrives (Pyth, Binance) |
| **Input** | Raw data saved as immutable log entry |
| **Prediction** | Each model produces an output |
| **Score** | Prediction compared to ground truth |
| **Snapshot** | Scores aggregated per model per period |
| **Leaderboard** | Models ranked by rolling window averages |
| **Checkpoint** | Rankings committed on-chain with Merkle proof |

---

# Design Principle #1

## Input is a Dumb Log

```python
InputRecord = { id, raw_data, received_at }
```

Saved once. **Never updated.**

No status field. No actuals. No scope.

> Ground truth is resolved from feed records, not from inputs.

This makes the system simple, append-only, and auditable.

---

# Design Principle #2

## Predictions Own Their Resolution

Each prediction carries everything needed to score it later:

```python
PredictionRecord = {
    id, model_id, input_id,
    scope_key,          # "BTC-60"
    scope_jsonb,        # {"subject": "BTC", "step_seconds": 15}
    inference_output,   # {"value": 0.73}
    resolvable_at,      # when ground truth is available
    status,             # PENDING → SCORED | FAILED | ABSENT
}
```

The score worker simply queries:
```sql
WHERE status = 'PENDING' AND resolvable_at <= now()
```

---

# Design Principle #3

## Type-Safe JSONB

**5 Pydantic types** define every data boundary:

| Type | Purpose |
|------|---------|
| `RawInput` | What the feed produces |
| `InferenceInput` | What models receive |
| `InferenceOutput` | What models return |
| `GroundTruth` | Actual outcome |
| `ScoreResult` | Score per prediction |

Every dict passes through `model_validate()` / `model_dump()`.

No wrappers. No extra serialization. **Pydantic IS the interface.**

---

# Design Principle #4

## Single Source of Truth

Everything is defined in **one file**: `CrunchConfig`

```python
class CrunchConfig(BaseModel):
    # Types
    output_type = InferenceOutput    # what models return
    score_type = ScoreResult         # what scoring produces

    # When to predict
    scheduled_predictions = [
        ScheduledPrediction(scope_key="BTC-60", ...)
    ]

    # How to aggregate
    aggregation = Aggregation(
        value_field="value",
        ranking_key="score_recent",
        windows={"score_recent": 24h, "score_steady": 72h}
    )

    # Callables
    scoring_function = my_scorer
    resolve_ground_truth = my_resolver
```

---

# Scoring: Two Modes

### Immediate (`resolve_horizon_seconds = 0`)

Ground truth from `InputRecord.raw_data` — for **live trading** where the current market state IS the truth.

### Deferred (`resolve_horizon_seconds > 0`)

Wait N seconds, then fetch feed records and resolve:

```python
def resolve_ground_truth(feed_records):
    entry_price = feed_records[0].values["close"]
    resolved_price = feed_records[-1].values["close"]
    return {
        "return": (resolved_price - entry_price) / entry_price,
        "direction_up": resolved_price > entry_price,
    }
```

**Stateful scoring** is supported — e.g. a PositionManager that tracks open positions across predictions.

---

# Leaderboard: Windowed Averaging

Snapshots are averaged over **rolling time windows**:

```
             ├─── score_recent (24h) ───┤
        ├──────── score_steady (72h) ─────────┤
   ├──────────── score_anchor (168h) ──────────────┤
   ▼                                               ▼
   t-168h ────────────────────────────────────── now
```

| Config | Default | Purpose |
|--------|---------|---------|
| `value_field` | `"value"` | Which score field to average |
| `ranking_key` | `"score_recent"` | Which window to rank by |
| `ranking_direction` | `"desc"` | Higher = better |

The leaderboard auto-generates UI columns by introspecting `score_type` fields.

---

# Merkle Tamper Evidence

Every score cycle builds a **Merkle tree** over its snapshots:

```
    Checkpoint Merkle Root (on-chain)
           /          \
    Cycle Root 1    Cycle Root 2
      /    \           /    \
   H(A,B)  H(C)    H(D,E)  ...
   /  \     |      /  \
  A    B    C     D    E     ← snapshot hashes
```

- **Snapshot hash** = `SHA256(model_id + period + sorted results)`
- **Cycles chain** — each stores `prev_cycle_root`
- **Checkpoint root** — tree over cycle roots, submitted on-chain

> Anyone can verify a snapshot was included in a checkpoint.
> Coordinators **cannot retroactively change scores**.

---

# The Feed System

Pluggable data providers with a simple protocol:

```python
class DataFeed(Protocol):
    async def list_subjects(self) -> list[SubjectDescriptor]: ...
    async def listen(self, subscription, sink) -> FeedHandle: ...
    async def fetch(self, request) -> list[FeedDataRecord]: ...
```

**Built-in:** Pyth Network, Binance (candles, ticks, depth, funding)

**4 dimensions** organize all data:

| Dimension | Examples |
|-----------|----------|
| Source | `pyth`, `binance` |
| Subject | `BTC`, `ETHUSDT` |
| Kind | `tick`, `candle`, `depth` |
| Granularity | `1s`, `1m`, `5m`, `1h` |

---

# Report API

The `report-worker` exposes **18+ REST endpoints**:

<div class="columns">
<div>

**Core**
- `/healthz`
- `/info`
- `/reports/schema`

**Models & Leaderboard**
- `/reports/models`
- `/reports/leaderboard`
- `/reports/models/global`
- `/reports/models/metrics`

</div>
<div>

**Pipeline Data**
- `/reports/predictions`
- `/reports/feeds`
- `/reports/snapshots`
- `/reports/checkpoints`

**Advanced**
- `/reports/diversity`
- `/reports/ensemble/history`
- `/reports/merkle/cycles`

</div>
</div>

Custom endpoints: drop a `.py` with a `router` in `node/api/` → auto-mounted.

---

# Getting Started: 3 Steps

### 1. Scaffold

```bash
crunch-cli init-workspace my-challenge
cd my-challenge
```

### 2. Customize

Edit **one file** — `node/config/crunch_config.py`:

- Define your `InferenceOutput` and `ScoreResult` types
- Set `scheduled_predictions` (what, how often, when to resolve)
- Implement your `scoring_function`

### 3. Deploy

```bash
make deploy       # builds + starts all 9 services
make verify-e2e   # validates the full pipeline
```

---

# Customization Example

```python
class MyOutput(BaseModel):
    direction: str = "hold"    # "long", "short", "hold"
    confidence: float = 0.0
    size: float = 0.0

class MyScore(BaseModel):
    pnl: float = 0.0
    sharpe: float = 0.0
    success: bool = True

class CrunchConfig(BaseCrunchConfig):
    output_type = MyOutput
    score_type = MyScore
    aggregation = Aggregation(value_field="pnl", ranking_key="score_recent")

    scheduled_predictions = [
        ScheduledPrediction(
            scope_key="BTC-live",
            scope={"subject": "BTC"},
            prediction_interval_seconds=60,
            resolve_horizon_seconds=0,
        ),
    ]

    scoring_function = position_manager.score  # stateful!
```

---

# What Ships in the Box

| Component | Description |
|-----------|-------------|
| **coordinator-node** (PyPI) | The engine — all workers, DB, scoring, feeds, Merkle |
| **scaffold/** | Template for new competitions |
| **Model Orchestrator** | Manages model containers (gRPC) |
| **Report UI** | Next.js dashboard (auto-configured) |
| **Alembic Migrations** | Schema management bundled in the wheel |
| **5 Example Models** | Simple, predictable — for E2E testing |
| **Backtest Harness** | Replay historical data through models |
| **Multi-Metric Scoring** | IC, Sharpe, hit rate, drawdown, diversity |
| **Ensemble Support** | Virtual meta-models combining participant outputs |

---

# Database: 10 Tables

```
feed_records ──→ inputs ──→ predictions ──→ scores
                              │                │
                              ▼                ▼
                   scheduled_prediction    snapshots ──→ merkle_nodes
                   _configs                    │              │
                                               ▼              ▼
                              models ──→ leaderboards    merkle_cycles
                                               │
                                               ▼
                                         checkpoints
```

All pipeline data in **JSONB columns** — typed by Pydantic models, queryable with PostgreSQL JSON operators.

---

# Deployment Architecture

**9 Docker containers** orchestrated by Docker Compose:

| Service | Port | Role |
|---------|------|------|
| PostgreSQL | 5432 | Database |
| init-db | — | Run migrations, exit |
| feed-data-worker | — | Ingest live data |
| predict-worker | — | Call models |
| score-worker | — | Score + leaderboard |
| checkpoint-worker | — | On-chain emissions |
| report-worker | 8000 | REST API |
| model-orchestrator | 9091 | Model containers |
| report-ui | 3000 | Dashboard |

```bash
make deploy      # one command to start everything
make verify-e2e  # one command to validate everything
```

---

# Status Lifecycles

### Predictions
```
PENDING ──→ SCORED      (scoring succeeded)
       ├──→ FAILED      (scoring error)
       └──→ ABSENT      (no ground truth available)
```

### Checkpoints
```
PENDING ──→ SUBMITTED ──→ CLAIMABLE ──→ PAID
            (tx sent)     (tx confirmed)  (rewards claimed)
```

Each checkpoint carries a **Merkle root** — cryptographic commitment to all scores in the period.

---

<!-- _class: lead -->

# Summary

## One engine. One config file. Any competition.

**Feed** → **Predict** → **Score** → **Rank** → **Checkpoint** → **On-chain**

Type-safe. Tamper-proof. Production-ready.

```bash
pip install coordinator-node
```

github.com/crunchdao/crunch-node-starter

---

# Q&A

### Resources

- **Docs**: `docs/` in the repo
- **Architecture**: `docs/architecture.md`
- **Config guide**: `docs/crunch-config.md`
- **API reference**: `docs/report-api.md`

### Key links

- PyPI: `pip install coordinator-node`
- GitHub: `github.com/crunchdao/crunch-node-starter`
- Scaffold: `crunch-cli init-workspace`
