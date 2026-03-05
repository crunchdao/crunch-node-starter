# Predict Service Kernel Refactor — Test Plan (Phase 1)

**Date:** 2026-03-05  
**Branch:** `core-predict-service-refactor`  
**Scope:** Phase 1 extraction (`ModelRegistry`, `OutputValidator`) with no behavior regressions.

## Objectives

1. Extract shared predict primitives without changing externally visible behavior.
2. Preserve current output validation semantics and prediction record shapes.
3. Introduce non-blocking handling for **non-critical** model metadata persistence failures.
4. Keep service-owned ingestion/streaming behavior untouched.

## Test Strategy

We use **test-first** for each extracted component, then run targeted regression suites.

### A. New unit tests (must fail first)

1. `ModelRegistry`
   - registers model in known set
   - persists model via repository when available
   - repository failure does not break flow (warn + continue)

2. `OutputValidator`
   - accepts valid output
   - rejects output when no keys match schema
   - rejects wrong types
   - normalizes/coerces valid values into output dict

### B. Existing regression tests to keep green

- `tests/test_output_validation.py`
- `tests/test_node_template_predict_service.py`
- `tests/test_tournament_predict_service.py`
- `tests/test_prediction_lifecycle.py`

These protect behavior for:
- prediction status mapping
- output validation error handling
- scope/resolvable timestamps
- tournament round workflows

### C. Architecture guard expectations

- No changes to realtime/tournament orchestration loops in this phase.
- No feed polling/scheduling logic moved into extracted components.
- Prediction persistence remains critical path.
- Model metadata persistence treated as non-critical.

## Execution Order (TDD)

1. Add failing tests for `ModelRegistry` and `OutputValidator`.
2. Implement minimal extraction to pass those tests.
3. Rewire `PredictService` to use extracted components.
4. Run regression suites above.
5. Run `make fmt` (if Python changed) and `make test`.

## Exit Criteria

- All new component tests pass.
- No regressions in targeted suites.
- `make test` passes.
- Refactor remains behavior-compatible for public/service-level contracts.

## Current Execution Snapshot

- Added/green component tests:
  - `tests/test_predict_components.py`
  - `tests/test_prediction_record_factory.py`
  - `tests/test_prediction_kernel.py`
  - `tests/test_predict_result_mapping.py`
- Targeted regression suites currently green (72 tests).
- Full `make test` remains environment-sensitive in this shell due to external
  Postgres host resolution in `tests/test_timing_endpoint.py`.
