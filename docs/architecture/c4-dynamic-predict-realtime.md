# C4 Dynamic — Realtime Predict Flow (Hot Path)

```mermaid
C4Dynamic
  title Dynamic Diagram - Realtime Predict Hot Path

  Container(feedWorker, "feed-data-worker", "Python", "Ingests feed and emits notifications")
  Component(realtime, "RealtimePredictService", "Service", "Realtime orchestration")
  Component(base, "PredictService", "Shared base facade", "Shared predict helpers")
  Component(kernel, "PredictionKernel", "Kernel", "Runner lifecycle + transport")
  Container(modelOrch, "Model Orchestrator", "gRPC", "Model execution")
  Component(factory, "PredictionRecordFactory", "Component", "Record construction")
  ContainerDb(postgres, "PostgreSQL", "SQL", "Pipeline storage")
  Component(registry, "ModelRegistry", "Component", "Deferred model metadata persistence")

  Rel(feedWorker, realtime, "1. Notify new feed data", "pg NOTIFY")
  Rel(realtime, base, "2. Run prediction cycle", "in-process")
  Rel(base, kernel, "3. Encode + dispatch feed_update/predict calls", "in-process")
  Rel(kernel, modelOrch, "4. Execute model methods", "gRPC")
  Rel(base, factory, "5. Build PredictionRecord(s)", "in-process")
  Rel(base, postgres, "6. Persist predictions (critical path)", "SQL")
  Rel(base, registry, "7. Flush deferred model metadata (non-critical)", "in-process")
  Rel(registry, postgres, "8. Persist model metadata (best effort)", "SQL")
```

## Latency + reliability implications

- Steps **3 → 6** define most of the predict roundtrip cost.
- The architecture target is to remain compatible with **~50ms optimized roundtrip**.
- Model metadata persistence is intentionally moved after critical prediction
  persistence to prevent non-essential DB failures from stalling prediction flow.
