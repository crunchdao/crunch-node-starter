# Predict Service Kernel Refactor — Design

**Date:** 2026-03-04
**Status:** Approved (direction), pending implementation

## Decision Summary

We will move to **Option B (composition)**:

- Keep service classes (`RealtimePredictService`, `TournamentPredictService`) as orchestration layers.
- Introduce a shared **prediction kernel/engine** for cross-mode invariants.
- Keep a shared **model registry** in the kernel.

This reduces `PredictService` scope and makes each mode-specific flow easier to read, test, and evolve.

## Target Responsibility Split

### Kernel (shared, reusable)
Owns only cross-mode behavior:

1. Runner lifecycle (`init_runner`, `shutdown`, credential wiring)
2. Model invocation primitives (call method + argument encoding)
3. Output normalization + schema validation
4. Domain factories (`Model`, `PredictionRecord`)
5. Shared persistence + logging helpers
6. Model registry (known model set, register/update semantics)

### Orchestrators (mode-specific)

- **RealtimePredictService**: feed wait loop, tick, scheduling, scope construction, absent policy, timing callbacks.
- **TournamentPredictService**: round/sample semantics, inference/scoring endpoints behavior, round matching.

### Explicit non-goals for kernel

- Feed ingestion
- Event loops or waits
- Scheduling policy
- Round lifecycle semantics
- Scoring policy

## Proposed Components

1. `PredictionKernel` (new)
   - Runner init/shutdown, model calls, shared save helper
2. `ModelRegistry` (new)
   - In-memory known models + repository sync behavior
3. `OutputValidator` (new)
   - `validate_and_normalize(output) -> (normalized_output, error)`
4. `PredictionRecordFactory` (new)
   - Build IDs, map status, construct `PredictionRecord`
5. `PredictService` (thin facade or compatibility layer)
   - Wires kernel + exposes shared helpers for subclasses

## Migration Plan (incremental, low risk)

### Phase 1 — Safe extraction without behavior change
- Extract `ModelRegistry` and `OutputValidator` from current `PredictService`.
- Keep method signatures compatible.
- Rewire both realtime/tournament services to use extracted classes.

### Phase 2 — Record construction extraction
- Extract `_build_record` into `PredictionRecordFactory`.
- Keep ID/status behavior byte-for-byte compatible.

### Phase 3 — Runner + transport extraction
- Move runner lifecycle and proto encoding into `PredictionKernel`.
- Keep current call_method behavior and tournament JSON special-case support.

### Phase 4 — Slim base service
- Reduce `PredictService` to composition wrapper around kernel.
- Remove orchestration leftovers from base class.

### Phase 5 — Cleanup
- Remove duplicate status/output handling paths where possible.
- Document extension points for future service modes.

## Testing Strategy

1. Add unit tests for each extracted component:
   - `ModelRegistry` register/update/absent behavior
   - `OutputValidator` schema mismatch/type mismatch/no-key-match cases
   - `PredictionRecordFactory` ID/status/meta invariants
   - `PredictionKernel` init/call/shutdown paths
2. Keep and run existing service-level tests for realtime and tournament flows.
3. Add regression tests that compare pre/post refactor behavior for:
   - prediction status mapping
   - inference_output persistence shape
   - absent model generation

## Acceptance Criteria

- `PredictService` no longer mixes orchestration concerns.
- Realtime and tournament orchestration logic remains in their own services.
- Existing public behavior and DB record shapes are preserved.
- `make test` passes.
- No functional regressions in benchmark and verify pipeline.

## Additional Constraints (2026-03-05)

1. **Predict latency architecture target**
   - Keep architecture compatible with ~50ms predict roundtrip when optimized.
   - Any expected material deviation must be explicitly surfaced with rationale and mitigation.

2. **Non-blocking persistence policy**
   - DB entries should not block model execution flow, except for critical correctness writes.
   - Non-critical persistence should be async/write-behind where practical.

3. **Service-owned ingestion/streaming**
   - Inheriting services decide how data is fetched/streamed and when inference is triggered.
   - Base/kernel layer is responsible for pushing data to models and shared cross-mode primitives.

See implementation checklist: `docs/plans/2026-03-05-predict-service-kernel-refactor-checklist.md`.

## Notes for Next Session

Start with **Phase 1** and open a small PR-sized change:
- Extract `ModelRegistry` + `OutputValidator`
- Rewire call sites
- Add focused unit tests
- Run `make fmt` and `make test`
