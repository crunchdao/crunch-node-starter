# Predict Service Kernel Refactor — Implementation Checklist & Invariants

**Date:** 2026-03-05  
**Branch:** `core-predict-service-refactor`  
**Scope:** Follow-up to `2026-03-04-predict-service-kernel-refactor-design.md`

## Non-Negotiable Architecture Invariants

1. **Predict latency target (~50ms)**
   - Architecture should allow ~50ms predict roundtrip when optimized.
   - If any design choice is expected to push materially above this, it must be explicitly called out with:
     1) reason, 2) estimated impact, 3) mitigation options.

2. **DB persistence must not block model flow (except critical path)**
   - Predict loop must not wait on non-critical DB writes.
   - Non-critical writes (e.g. model registry metadata, optional timing/telemetry enrichment) should use async/write-behind paths.
   - Critical writes (minimum needed for correctness, e.g. prediction records required for scoring) may remain synchronous, but must be bounded and observable.

3. **Service-owned ingestion/streaming policy**
   - Concrete services (`RealtimePredictService`, `TournamentPredictService`, future subclasses) own:
     - data fetch/stream wait strategy
     - scheduling and trigger policy
     - scope construction semantics
   - Base/kernel layer owns only model invocation primitives + shared normalization/factory/persistence helpers.

4. **Composition over inheritance leakage**
   - Base class must not assume feed semantics.
   - Tournament-specific and realtime-specific behavior must not be encoded in kernel primitives.

---

## Phase 1 Checklist (extract without behavior change)

### A) Extract components
- [x] `ModelRegistry` extracted from `PredictService`
- [x] `OutputValidator` extracted from `PredictService`
- [x] Existing service callsites rewired to extracted components
- [x] Public behavior and DB shapes remain unchanged (validated via targeted regression suite)

### A.2) Phase 2 progress — record construction extraction
- [x] `PredictionRecordFactory` extracted from `PredictService._build_record`
- [x] ID/status/scope/meta invariants preserved via component tests

### A.3) Phase 3 progress — runner + transport extraction
- [x] `PredictionKernel` extracted with runner lifecycle + encoding/call primitives
- [x] `PredictService` delegates init/shutdown/call/encode via kernel
- [x] Compatibility preserved for direct `self._runner` swaps in tests/legacy flows

### A.4) Phase 4 progress — base service slimming
- [x] `PredictService` accepts `feed_reader=None` for non-feed modes
- [x] `get_data()` fails fast with explicit error when no feed reader is configured
- [x] `ModelRegistry` changed to non-blocking registration + deferred non-critical flush
- [x] Non-critical model metadata persistence now occurs after critical prediction persistence

### A.5) Phase 5 cleanup progress
- [x] Extracted shared runner-result mapping into `PredictService._map_runner_result`
- [x] Realtime and tournament services now reuse the same status/output normalization path
- [x] Added focused tests for status mapping and validation-failure mapping

### B) Define critical vs non-critical persistence
- [x] Document persistence classification in code comments/docstring
- [x] Keep prediction persistence path explicitly marked as critical
- [x] Move non-critical writes off hot path (or add TODO + tracked follow-up if phased)
  - Implemented: register path is non-blocking for DB writes; model metadata persistence is deferred and flushed after critical prediction persistence

### C) Add architecture guards
- [ ] Add explicit comments/contracts: services own ingestion/streaming, kernel owns push-to-model path
- [ ] Ensure no feed wait/scheduling logic is introduced into kernel abstractions

### D) Tests
- [x] Unit tests: `ModelRegistry` (register/update/known set semantics)
- [x] Unit tests: `OutputValidator` (valid, type mismatch, no-key-match)
- [x] Unit tests: `PredictionRecordFactory` ID/status/meta invariants
- [x] Unit tests: `PredictionKernel` init/call/encode/shutdown paths
- [x] Regression tests: status mapping + inference_output persistence shape unchanged (targeted suites)
- [ ] Latency guard test (or benchmark harness assertion) for no regression in predict hot path

### E) Verification
- [ ] `make test` (blocked in local env by external Postgres host resolution in timing endpoint test)
- [x] If Python files changed: `make fmt`
- [ ] Optional runtime verification: `make deploy && make verify-e2e`

---

## Implementation Guidance for the Two New Concerns

### 1) Non-blocking DB policy (pragmatic)

Use a **split persistence policy**:
- **Critical sync path:** prediction records needed for downstream scoring/reports.
- **Async path:** model metadata updates, timing enrichment, auxiliary counters.

Recommended mechanics:
- small in-process async queue for non-critical writes
- bounded queue size + explicit drop/backpressure policy
- structured warning logs when queue is saturated
- periodic flush + graceful shutdown drain with timeout

### 2) Service-controlled ingestion/streaming

Keep this contract explicit:
- Services call kernel methods like:
  - `init_runner()`
  - `call_models()`
  - `validate_and_normalize_output()`
  - `build_prediction_record()`
  - `save_predictions()`
- Services must provide their own:
  - `wait_for_data()` / trigger semantics
  - schedule cadence
  - scope semantics and absent-model policy

---

## PR Review Checklist (must pass)

- [ ] Does this change preserve service-level ownership of data fetch/stream behavior?
- [ ] Does it keep kernel free of scheduling/feed lifecycle logic?
- [ ] Does it avoid adding DB blocking work on the predict hot path?
- [ ] If latency risk exists, is the deviation explicitly documented with impact + mitigation?
- [ ] Are record IDs/status/output shapes backward compatible?
