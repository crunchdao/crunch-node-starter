# C4 Level 3 — Component Diagram (Predict Worker / Kernel Refactor)

```mermaid
C4Component
  title Component Diagram - Predict Worker (Kernel + Mode Orchestrators)

  Container(modelOrch, "Model Orchestrator", "gRPC", "Executes model methods")
  ContainerDb(postgres, "PostgreSQL", "SQLModel", "Prediction pipeline state")

  Container_Boundary(predictWorker, "predict-worker") {
    Component(realtime, "RealtimePredictService", "Service", "Owns wait loop, scheduling, scope timing, absent policy")
    Component(tournament, "TournamentPredictService", "Service", "Owns round/sample semantics and inference/scoring endpoints")

    Component(base, "PredictService", "Shared base facade", "Shared helpers + contracts used by concrete orchestrators")

    Component(kernel, "PredictionKernel", "Kernel", "Runner lifecycle, call transport, predict/feed_update argument encoding")
    Component(registry, "ModelRegistry", "Component", "Tracks known models; deferred non-critical metadata persistence")
    Component(validator, "OutputValidator", "Component", "Output schema validation and normalization")
    Component(factory, "PredictionRecordFactory", "Component", "Stable PredictionRecord ID/status/meta construction")

    Component(inputRepo, "InputRepository adapter", "DB adapter", "Persists input records")
    Component(predRepo, "PredictionRepository adapter", "DB adapter", "Persists prediction records")
    Component(modelRepo, "ModelRepository adapter", "DB adapter", "Persists model metadata")
  }

  Rel(realtime, base, "Uses shared prediction helpers")
  Rel(tournament, base, "Uses shared prediction helpers")

  Rel(base, kernel, "Initializes runner and performs model calls")
  Rel(base, validator, "Validates/normalizes model outputs")
  Rel(base, factory, "Builds PredictionRecord")
  Rel(base, registry, "Registers models and flushes deferred metadata")

  Rel(base, inputRepo, "Writes inputs")
  Rel(base, predRepo, "Writes predictions (critical path)")
  Rel(base, modelRepo, "Writes model metadata (non-critical deferred flush)")

  Rel(kernel, modelOrch, "feed_update/predict calls", "gRPC")
  Rel(inputRepo, postgres, "SQL")
  Rel(predRepo, postgres, "SQL")
  Rel(modelRepo, postgres, "SQL")
```

## Design rules captured by this component split

1. **Service-owned ingestion/streaming:** realtime/tournament services own their own trigger/data semantics.
2. **Kernel-owned model push path:** runner lifecycle, encoding, and model calls are centralized in `PredictionKernel`.
3. **Critical vs non-critical writes:** predictions are critical; model metadata is deferred/non-blocking.
4. **Single result mapping policy:** shared result/status/output mapping is centralized in base service logic.
