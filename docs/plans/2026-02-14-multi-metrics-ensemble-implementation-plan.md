# Multi-Metric Scoring & Ensemble — Implementation Plan

Reference: [Design Doc](./2026-02-14-multi-metrics-ensemble-design.md)

## Phase 1: Metrics Infrastructure

### Task 1.1: MetricsContext
**File:** `coordinator_node/metrics/context.py` (new)
- `MetricsContext` dataclass with model_id, window boundaries, all_model_predictions, ensemble_predictions

### Task 1.2: MetricsRegistry
**File:** `coordinator_node/metrics/registry.py` (new)
- `MetricsRegistry` class: register(name, fn), compute(metrics, predictions, scores, context) → dict
- Default global registry instance with builtins auto-registered

### Task 1.3: Built-in metrics
**File:** `coordinator_node/metrics/builtins.py` (new)
- `ic` — Spearman rank correlation
- `ic_sharpe` — mean(IC) / std(IC)
- `mean_return` — mean return from signals
- `hit_rate` — directional accuracy
- `max_drawdown` — worst peak-to-trough
- `sortino_ratio` — downside-only Sharpe
- `turnover` — signal change rate
- `model_correlation` — mean pairwise correlation to other models

### Task 1.4: Package init
**File:** `coordinator_node/metrics/__init__.py` (new)

### Task 1.5: Tests for metrics
**Files:** `tests/test_metrics_registry.py`, `tests/test_metrics_builtins.py` (new)

---

## Phase 2: Contract Integration

### Task 2.1: Add metrics and ensemble config to contract
**File:** `coordinator_node/contracts.py` (modify)
- Add `metrics: list[str]` field with defaults
- Add `EnsembleConfig` model
- Add `ensembles: list[EnsembleConfig]` field (empty default)
- Add `compute_metrics` callable field

### Task 2.2: Tests
**File:** `tests/test_coordinator_core_schema.py` (modify) — verify new fields

---

## Phase 3: Score Pipeline Enrichment

### Task 3.1: Enrich _write_snapshots with metrics
**File:** `coordinator_node/services/score.py` (modify)
- Build MetricsContext from predictions/scores
- Call compute_metrics per model
- Merge metric results into snapshot result_summary

### Task 3.2: Tests
**File:** `tests/test_multi_metric_scoring.py` (new)

---

## Phase 4: Ensemble Service

### Task 4.1: EnsembleService
**File:** `coordinator_node/services/ensemble.py` (new)
- `inverse_variance(model_metrics, predictions) → weights`
- `equal_weight(model_metrics, predictions) → weights`
- `top_n(n)` filter factory
- `min_metric(name, threshold)` filter factory
- `EnsembleService.compute(config, predictions, scores, metrics) → list[PredictionRecord]`

### Task 4.2: Ensemble metrics
**File:** `coordinator_node/metrics/ensemble_metrics.py` (new)
- `contribution` — leave-one-out score difference
- `ensemble_correlation` — correlation to ensemble output
- `fnc` — feature-neutral correlation

### Task 4.3: Tests
**File:** `tests/test_ensemble_service.py` (new)

---

## Phase 5: Ensemble in Score Pipeline

### Task 5.1: Wire ensemble into score cycle
**File:** `coordinator_node/services/score.py` (modify)
- After scoring + metrics: run ensembles if configured
- Register virtual models, store ensemble predictions
- Score ensemble predictions, compute ensemble metrics

### Task 5.2: Tests — end-to-end
**File:** `tests/test_multi_metric_scoring.py` (extend)

---

## Phase 6: Leaderboard Filtering

### Task 6.1: Add include_ensembles param
**File:** `coordinator_node/workers/report_worker.py` (modify)
- Add `include_ensembles: bool = False` to leaderboard, models/global, models/params
- Filter out `__ensemble_*` model IDs when false

### Task 6.2: Tests
**File:** `tests/test_backfill_endpoints.py` or `tests/test_node_template_report_worker.py` (extend)

---

## Phase 7: Backtest Integration

### Task 7.1: Multi-metric BacktestResult
**File:** `scaffold/challenge/starter_challenge/backtest.py` (modify)
- BacktestRunner computes active metrics using registry
- BacktestResult.metrics contains full metric dict

---

## Implementation Order

1. Phase 1 (Tasks 1.1–1.5) — metrics infrastructure + tests
2. Phase 2 (Tasks 2.1–2.2) — contract fields
3. Phase 3 (Tasks 3.1–3.2) — score pipeline enrichment
4. Phase 4 (Tasks 4.1–4.3) — ensemble service + tests
5. Phase 5 (Tasks 5.1–5.2) — wire ensemble into pipeline
6. Phase 6 (Tasks 6.1–6.2) — leaderboard filtering
7. Phase 7 (Task 7.1) — backtest integration
