# Project Context — starter-challenge

## What this is

A Crunch coordinator workspace running a competition pipeline. Two packages in one workspace:

- `node/` — competition infrastructure (docker-compose, workers, config, API)
- `challenge/` — participant-facing package (tracker interface, scoring, backtest, examples)

The node runs `coordinator-node` (published to PyPI) as its engine.

## Architecture

### Pipeline

```
Feed → Input → Prediction → Score → Snapshot → Checkpoint → On-chain
```

### Workers

| Worker | Purpose |
|---|---|
| `feed-data-worker` | Ingests feed data (Pyth, Binance, etc.) via polling + backfill |
| `predict-worker` | Event-driven: feed → models → predictions |
| `score-worker` | Resolves actuals → scores → snapshots → leaderboard |
| `checkpoint-worker` | Aggregates snapshots → EmissionCheckpoint |
| `report-worker` | FastAPI server: leaderboard, predictions, feeds, snapshots, checkpoints |

### Contract-Based Design

All type shapes and behavior are defined in a single `CrunchConfig` in `node/runtime_definitions/crunch_config.py`:

```python
class CrunchConfig(BaseModel):
    # Type shapes
    meta_type: type[BaseModel] = Meta                  # untyped metadata (extra="allow")
    raw_input_type: type[BaseModel] = RawInput         # feed data shape
    ground_truth_type: type[BaseModel] = GroundTruth   # actual outcome shape
    input_type: type[BaseModel] = InferenceInput       # what models receive
    output_type: type[BaseModel] = InferenceOutput     # what models return
    score_type: type[BaseModel] = ScoreResult          # per-prediction score shape
    scope: PredictionScope = PredictionScope()         # prediction context
    call_method: CallMethodConfig = CallMethodConfig() # how models are invoked
    aggregation: Aggregation = Aggregation()           # scoring windows + ranking

    # Multi-metric scoring (default: 5 active metrics)
    metrics: list[str] = ["ic", "ic_sharpe", "hit_rate", "max_drawdown", "model_correlation"]
    compute_metrics: Callable = default_compute_metrics

    # Ensemble (default: off)
    ensembles: list[EnsembleConfig] = []

    # Callables
    resolve_ground_truth: Callable[[list[FeedRecord]], dict[str, Any] | None] = default_resolve_ground_truth
    aggregate_snapshot: Callable[[list[dict[str, Any]]], dict[str, Any]] = default_aggregate_snapshot
    build_emission: Callable[..., EmissionCheckpoint] = default_build_emission

    # On-chain config
    crunch_pubkey: str = ""
    compute_provider: str | None = None
    data_provider: str | None = None
```

#### Key sub-schemas

```python
class PredictionScope(BaseModel):
    subject: str = "BTC"           # asset or topic
    horizon_seconds: int = 60      # ge=0; use 0 for order-based / immediate-scoring
    step_seconds: int = 15         # ge=1; time granularity within horizon

class AggregationWindow(BaseModel):   # extra="forbid"
    hours: int                         # ge=1 — the ONLY field (no name, no seconds)

class Aggregation(BaseModel):          # extra="forbid"
    windows: dict[str, AggregationWindow] = {   # dict keyed by name, NOT a list
        "score_recent": AggregationWindow(hours=24),
        "score_steady": AggregationWindow(hours=72),
        "score_anchor": AggregationWindow(hours=168),
    }
    ranking_key: str = "score_recent"
    ranking_direction: str = "desc"    # NOT "ranking_order"

class CallMethodConfig(BaseModel):
    method: str = "predict"
    args: list[CallMethodArg] = [
        CallMethodArg(name="subject", type="STRING"),
        CallMethodArg(name="horizon_seconds", type="INT"),
        CallMethodArg(name="step_seconds", type="INT"),
    ]
```

**Note:** `resolve_ground_truth` receives `list[FeedRecord]` (dataclass with
`.subject`, `.values`, `.ts_event` attributes), NOT `list[dict]`.

Single required callable: `SCORING_FUNCTION` (in `node/config/callables.env`).

### Feed Dimensions

Four generic dimensions: **source**, **subject**, **kind**, **granularity**.
Env vars: `FEED_SOURCE`, `FEED_SUBJECTS`, `FEED_KIND`, `FEED_GRANULARITY`.

### Status Lifecycles

```
Input:       RECEIVED → RESOLVED
Prediction:  PENDING → SCORED / FAILED / ABSENT
Checkpoint:  PENDING → SUBMITTED → CLAIMABLE → PAID
```

### Multi-Metric Scoring

Portfolio-level metrics computed per model per score cycle. Stored in snapshot `result_summary` JSONB.

- Opt out: `metrics=[]`
- Opt into specific metrics: `metrics=["ic", "sortino_ratio"]`
- Custom metrics: `get_default_registry().register("name", fn)`
- Ranking key can be any active metric name

### Ensemble Framework

Combine model predictions into virtual meta-models. Off by default.

- Opt in: `ensembles=[EnsembleConfig(...)]`
- Strategies: `inverse_variance`, `equal_weight`
- Filters: `top_n(n)`, `min_metric(name, threshold)`
- Virtual models `__ensemble_{name}__` flow through normal scoring pipeline
- Hidden from leaderboard by default (`include_ensembles=false`)

### Emission Checkpoints

Protocol-matching format with frac64 reward percentages (1,000,000,000 = 100%).
Default tier distribution: 1st=35%, 2nd-5th=10% each, 6th-10th=5% each.
Unclaimed share redistributed equally across all ranked entries.

---

## Quick reference

### Fast path (from workspace root)

```bash
cd node
make deploy
make verify-e2e
make logs-capture
```

### Where to edit code

| What to change | Where to edit |
|---|---|
| Challenge behavior (tracker, scoring, examples) | `challenge/starter_challenge/` |
| Runtime contract (types, callables, emission config) | `node/runtime_definitions/crunch_config.py` |
| Node config (env, deployment, schedules) | `node/` (.local.env, config/, deployment/) |

For detailed edit boundaries, see `node/.agent/context.md` and `challenge/.agent/context.md`.

### Where logs and diagnostics live

- Live service logs: `cd node && make logs`
- Captured runtime logs: `node/runtime-services.jsonl`
- Lifecycle audit: `process-log.jsonl`
- Additional troubleshooting: `node/RUNBOOK.md`

---

## Where to put new code

| I want to… | Put it in |
|---|---|
| Add a new API endpoint | `node/api/` — drop a `.py` file with `router = APIRouter(prefix="/custom")`. Auto-mounted at report-worker startup. |
| Override scoring, ground truth, aggregation, or emission logic | `node/runtime_definitions/crunch_config.py` — override the relevant `CrunchConfig` callable field |
| Add a custom feed provider or external API integration | `node/plugins/` — for node-side integrations that need secrets or call private APIs |
| Add a scoring helper or custom callable module | `node/extensions/` — for edge-case Python modules needed by the runtime (custom feed providers, specialized scoring helpers) |
| Change the scoring function path | `node/config/callables.env` — set `SCORING_FUNCTION=module.path:function` |
| Change prediction schedule or scope | `node/config/scheduled_prediction_configs.json` — **`resolve_after_seconds` must be > feed data interval** (see `node/.agent/context.md`) |
| Change feed source, subjects, kind, granularity | `node/.local.env` — `FEED_SOURCE`, `FEED_SUBJECTS`, `FEED_KIND`, `FEED_GRANULARITY` |
| Change the model interface participants implement | `challenge/starter_challenge/tracker.py` |
| Change local self-eval scoring | `challenge/starter_challenge/scoring.py` |
| Add a quickstarter example | `challenge/starter_challenge/examples/` |
| Customize local deployment (model-orchestrator, report-ui) | `node/deployment/` |
| Customize environment for production | `node/.production.env.example` (template only — actual prod env is not committed) |

## Extension points (detailed)

### To add a new API endpoint

1. Create a `.py` file in `node/api/` with a `router = APIRouter(prefix="/custom")`
2. Deploy: `cd node && make deploy`
3. Endpoint auto-mounts at report-worker startup
4. Full DB access via FastAPI `Depends` — see `node/api/README.md` for examples

### To add a custom metric

1. In `node/runtime_definitions/crunch_config.py`, register with `get_default_registry().register("name", fn)`
2. Add the metric name to `CrunchConfig.metrics` list

### To add a new ensemble strategy

1. Define strategy function: `(model_metrics, predictions) → {model_id: weight}`
2. Add `EnsembleConfig(name="...", strategy=your_fn)` to `CrunchConfig.ensembles`

### To add a custom feed provider or external integration

1. Create a module in `node/plugins/`
2. Wire it into the runtime via `CrunchConfig` callables or env vars
3. Keep secrets in `node/.local.env`, never in the challenge package

### To add a scoring helper or callable override

1. Create a module in `node/extensions/`
2. Reference it from `node/config/callables.env` or `CrunchConfig`
3. Most customization should go directly in `CrunchConfig` — use `extensions/` only for edge cases

### To change the scoring function

1. Edit callable path in `node/config/callables.env` (`SCORING_FUNCTION=...`)
2. Or override directly in `CrunchConfig`

### To change feed source or subjects

1. Edit `FEED_SOURCE`, `FEED_SUBJECTS`, `FEED_KIND`, `FEED_GRANULARITY` in `node/.local.env`

### To customize emission tiers

1. Override `build_emission` in `CrunchConfig`
2. For contribution-weighted: use `contribution_weighted_emission` from `coordinator_node.extensions.emission_strategies`

---

## Tests — scaffold health checks

Two test suites verify the scaffold is wired correctly. Run them before and
after any customization.

### Challenge tests (`challenge/tests/`)

```bash
make test          # from workspace root (base/)
```

Tests the participant-facing package: tracker data isolation, scoring contract,
example model compliance. Scoring behavioral tests are `xfail(strict=True)` —
they fail against the 0.0 stub and must be updated when real scoring is implemented.

### Scaffold integration tests (`tests/test_scaffold_integration.py`)

```bash
cd .. && PYTHONPATH=base/challenge:base/node make test    # from repo root
```

Tests CrunchConfig wiring end-to-end without Docker:

- `scheduled_prediction_configs.json` validates as `ScheduledPredictionConfigEnvelope`
- `scope_template` keys match `PredictionScope` fields
- `CallMethodConfig.args` resolvable from merged scope
- `resolve_ground_truth` returns non-None for valid feed data
- Scoring function accepts `InferenceOutput` defaults, output validates as `ScoreResult`
- `aggregate_snapshot` produces data, `ranking_key` exists in pipeline output
- Example tracker `predict()` output validates as `InferenceOutput`
- Full roundtrip: tracker → scoring function (no KeyError)

**These are TDD targets for customization.** When changing types, scoring,
scopes, or feeds, check which tests break and fix them as part of the work.
All must be green before deploying.

## Do-not-edit zones

- `node/docker-compose.yml` internal service wiring — modify env vars instead
- Engine internals (`coordinator_node/` package) — override via `CrunchConfig` callables
- `node/scripts/` — utility scripts, not competition logic
- `challenge/starter_challenge/config.py` — auto-generated, edit only `COORDINATOR_URL` for publishing
