# Predict Service Kernel Architecture (Refactor Guide)

This document explains the refactored predict architecture in practical terms.

## Why this refactor exists

Before refactor, `PredictService` mixed several concerns:

- model-runner lifecycle and transport encoding
- output validation and result mapping
- prediction record construction
- model registry updates
- orchestration-adjacent behavior leaking into shared code

The new architecture separates these concerns so each part has one job,
improving readability, testability, and extension safety.

## New structure

### Mode orchestrators (own workflow semantics)

- `RealtimePredictService`
  - data wait loop
  - schedule gating
  - scope timing fields
  - absent policy for realtime cycles
- `TournamentPredictService`
  - round/sample semantics
  - request-driven inference/scoring flow

### Shared predict layer (owns cross-mode primitives)

- `PredictService` (thin shared facade)
  - shared helper methods consumed by both modes
  - shared runner-result mapping logic
- `PredictionKernel`
  - runner init/shutdown
  - feed_update/predict argument encoding
  - model call transport
- `OutputValidator`
  - output schema matching + normalization
- `PredictionRecordFactory`
  - stable ID/status/meta/scope normalization
- `ModelRegistry`
  - known-model tracking
  - deferred non-critical metadata persistence

## Critical architectural rules

1. **Service-owned ingestion/streaming**
   - Subclasses decide how data is fetched/streamed and when prediction runs.
2. **Kernel-owned model push path**
   - Shared kernel handles transport/lifecycle/encoding.
3. **Critical vs non-critical persistence**
   - Prediction writes are critical and stay on the hot path.
   - Model metadata writes are non-critical and flushed after critical writes.
4. **Latency target awareness**
   - Architecture should stay compatible with ~50ms optimized predict roundtrip.
   - Any design expected to materially exceed this should be explicitly called out.

## Compatibility decisions

- `PredictService` now supports `feed_reader=None` for non-feed modes.
- `get_data()` fails fast with a clear error if called without a feed reader.
- Compatibility with tests/legacy flows that directly swap `self._runner` is preserved.

## Where to start reading code

1. `crunch_node/services/predict_components.py`
2. `crunch_node/services/predict.py`
3. `crunch_node/services/realtime_predict.py`
4. `crunch_node/services/tournament_predict.py`
5. Tests:
   - `tests/test_predict_components.py`
   - `tests/test_prediction_kernel.py`
   - `tests/test_prediction_record_factory.py`
   - `tests/test_predict_result_mapping.py`
