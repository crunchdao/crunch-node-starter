# Multi-Metric Scoring & Ensemble Framework Design

## Overview

Add a pluggable multi-metric scoring framework and a model ensembling system to the coordinator node. Competitions declare which metrics to compute, and optionally configure ensemble strategies that produce virtual "meta-model" predictions.

## Current State

The system has a single `scoring_function(prediction, ground_truth) → ScoreResult` returning one `value: float`. Everything downstream — snapshots, leaderboards, checkpoints, emissions — keys off that single value through rolling windows. JSONB columns can store arbitrary dicts but nothing computes or consumes multi-metric data today.

## 1. Metrics Registry

A library of metric functions shipped with coordinator-node. Each metric has a name, compute function, and metadata about required prediction shape.

### Built-in Metrics

**Tier 1 — Core (ranking-eligible):**
- `ic` — Information Coefficient: Spearman rank correlation between predictions and realized returns
- `ic_sharpe` — mean(IC) / std(IC) over the window. Rewards consistency.
- `mean_return` — mean return of a long-short portfolio formed from signals
- `hit_rate` — % of predictions with correct sign
- `model_correlation` — mean pairwise Spearman correlation against all other active models

**Tier 2 — Risk/stability (diagnostic):**
- `max_drawdown` — worst peak-to-trough on cumulative IC or returns
- `sortino_ratio` — like Sharpe but only penalizes downside volatility
- `turnover` — how much the signal changes between consecutive predictions

**Tier 3 — Ensemble-relevant (computed when ensembling enabled):**
- `fnc` — Feature-Neutral Correlation: IC after orthogonalizing against known factors
- `contribution` — marginal improvement to ensemble when this model is added (leave-one-out)
- `ensemble_correlation` — correlation of this model's predictions to the ensemble output

### Metric Function Signature

```python
def metric_fn(
    predictions: list[dict],
    scores: list[dict],
    context: MetricsContext,
) -> float:
```

### MetricsContext

Passed to each metric function, built once per score cycle:

```python
@dataclass
class MetricsContext:
    model_id: str
    window_start: datetime
    window_end: datetime
    all_model_predictions: dict[str, list[dict]]  # model_id → predictions
    ensemble_predictions: dict[str, list[dict]]    # ensemble_name → predictions
```

### Registry API

```python
registry = MetricsRegistry()
registry.register("ic", compute_ic)
registry.register("my_custom", my_fn)

results = registry.compute(
    metrics=["ic", "ic_sharpe", "hit_rate"],
    predictions=predictions,
    scores=scores,
    context=context,
)
# → {"ic": 0.035, "ic_sharpe": 1.2, "hit_rate": 0.58}
```

Custom metrics registered via `MetricsRegistry.register(name, fn)`.

## 2. Contract Integration

`CrunchContract` gains:

```python
class CrunchContract(BaseModel):
    # ... existing fields ...

    # Metrics
    metrics: list[str] = ["ic", "ic_sharpe", "hit_rate", "max_drawdown", "model_correlation"]
    compute_metrics: Callable = default_compute_metrics

    # Ensembles
    ensembles: list[EnsembleConfig] = []
```

Competitions override the `metrics` list to declare which metrics are active. Only listed metrics are computed. The `ranking_key` in `Aggregation` can point to any metric name (e.g. `ranking_key="ic_sharpe"`).

## 3. Metrics Computation in Score Pipeline

Runs in `ScoreService._write_snapshots()` after grouping scores by model:

1. `aggregate_snapshot(results)` — baseline aggregation (unchanged)
2. `compute_metrics(predictions, scores, context)` — per-model, computes each active metric
3. Merge into single `result_summary` dict

Snapshot `result_summary` goes from `{"value": 0.42}` to:
```json
{
  "value": 0.42,
  "ic": 0.035,
  "ic_sharpe": 1.2,
  "hit_rate": 0.58,
  "max_drawdown": -0.12,
  "model_correlation": 0.31,
  "sortino_ratio": 1.8,
  "turnover": 0.15
}
```

Leaderboard ranking unchanged — `aggregation.ranking_key` now can point to any metric. Rolling windows average the metric across snapshots in the window. No leaderboard code changes needed.

## 4. Ensemble Framework

### EnsembleConfig

```python
class EnsembleConfig(BaseModel):
    name: str                              # "main", "top5", "equal_weight"
    strategy: Callable = inverse_variance  # weight function
    model_filter: Callable | None = None   # optional: which models to include
    enabled: bool = True
```

Empty by default — ensembling is opt-in.

### Weight Function Signature

```python
def strategy(
    model_metrics: dict[str, dict[str, float]],  # model_id → metrics
    predictions: dict[str, list[dict]],           # model_id → recent predictions
) -> dict[str, float]:                            # model_id → weight
```

### Built-in Strategies

- `inverse_variance` (default) — weight = 1/var(scores), normalized to sum to 1.0. Stable with 2+ models.
- `equal_weight` — 1/N for all included models.
- Mean-variance optimization available for competitions with enough models (pluggable).

### Model Filters

- `top_n(n)` — keep only the N highest-ranked models
- `min_metric(name, threshold)` — keep models above a metric threshold

Signature: `model_filter(model_id: str, metrics: dict[str, float]) → bool`

### Ensemble Predictions

For each enabled ensemble config:

1. Filter models via `model_filter`
2. Compute weights via `strategy`
3. Weighted-average the filtered models' `inference_output["value"]`
4. Store as `PredictionRecord` with `model_id="__ensemble_{name}__"`
5. Store weights in `prediction.meta["weights"]`

Virtual models `__ensemble_{name}__` are registered automatically and flow through the normal pipeline — scored, metrics computed, appear on leaderboard.

### Multiple Ensembles

The contract supports a list of ensemble configs. Each produces its own virtual model:
```python
ensembles=[
    EnsembleConfig(name="main", strategy=inverse_variance),
    EnsembleConfig(name="top5", strategy=inverse_variance, model_filter=top_n(5)),
    EnsembleConfig(name="equal", strategy=equal_weight),
]
```

### Contribution Metric (Tier 3)

Computed by leave-one-out: re-run ensemble without model_i, measure score difference. This tells each model how much it helps the meta-model.

## 5. Leaderboard Filtering

Leaderboard and model endpoints gain an `include_ensembles: bool = False` query parameter. Default view shows only real models. Ensemble virtual models are identifiable by `__ensemble_` prefix.

Affected endpoints:
- `GET /reports/leaderboard`
- `GET /reports/models/global`
- `GET /reports/models/params`

Data is always stored — filtering is display-level only.

## 6. Complete Score Cycle

```
1. resolve inputs (unchanged)
2. score predictions (unchanged — per-prediction scoring_function)
3. compute per-model metrics
   - build MetricsContext (all predictions, cross-model data)
   - for each model: run active metrics from registry
   - merge into enriched result_summary
4. compute ensembles (if any enabled)
   - for each EnsembleConfig:
     a. filter models (model_filter)
     b. compute weights (strategy)
     c. weighted-average predictions → ensemble PredictionRecord
     d. score ensemble prediction
     e. compute ensemble metrics
5. write snapshots (enriched with all metrics, all models including ensembles)
6. rebuild leaderboard
   - rank by contract.aggregation.ranking_key (now any metric)
   - ensemble models included in data, filterable in API
```

Steps 3-4 are new. Steps 1-2 and 5-6 are existing with richer data flowing through. No new workers, no new tables, no new lifecycles.

## 7. Key Design Decisions

- **Metrics are a registry, not hardcoded** — competitions declare which metrics to compute from a list. Custom metrics can be registered.
- **Per-prediction scoring unchanged** — `scoring_function` stays per-prediction. Metrics are a separate additive layer that reads scored predictions.
- **Metrics stored in snapshot JSONB** — no new tables. Snapshots already have `result_summary` as unconstrained JSONB.
- **Ensemble as virtual model** — ensemble predictions stored as regular `PredictionRecord` rows from synthetic models. Gets scoring, metrics, leaderboard for free.
- **Multiple ensembles supported** — each `EnsembleConfig` produces a named virtual model.
- **Inverse-variance as default strategy** — stable with few models, no covariance estimation needed. Portfolio optimization pluggable for larger competitions.
- **Leaderboard filterable** — `include_ensembles=false` by default so competitors see real-model rankings.
- **Not all metrics apply everywhere** — explicit `metrics: list[str]` on contract, no magic profiles.

## 8. Files to Create/Modify

### New
- `coordinator_node/metrics/__init__.py`
- `coordinator_node/metrics/registry.py` — MetricsRegistry, register(), compute()
- `coordinator_node/metrics/context.py` — MetricsContext dataclass
- `coordinator_node/metrics/builtins.py` — IC, IC Sharpe, hit rate, max drawdown, Sortino, turnover, model correlation
- `coordinator_node/metrics/ensemble_metrics.py` — FNC, contribution, ensemble correlation
- `coordinator_node/services/ensemble.py` — EnsembleService, inverse_variance, equal_weight, top_n, min_metric filters

### Modified
- `coordinator_node/contracts.py` — add metrics list, compute_metrics callable, EnsembleConfig, ensembles list
- `coordinator_node/services/score.py` — enrich _write_snapshots() with metrics, add ensemble step
- `coordinator_node/workers/report_worker.py` — add include_ensembles param to leaderboard/model endpoints
- `scaffold/challenge/starter_challenge/backtest.py` — BacktestResult computes multi-metrics using same registry

### Tests
- `tests/test_metrics_registry.py` — registry CRUD, compute dispatching
- `tests/test_metrics_builtins.py` — each metric with synthetic data
- `tests/test_ensemble_service.py` — weights, filtering, virtual models, leave-one-out
- `tests/test_multi_metric_scoring.py` — end-to-end: predictions → enriched snapshots → leaderboard
