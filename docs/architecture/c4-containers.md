# C4 Level 2 — Container Diagram (Crunch Coordinator Node)

```mermaid
C4Container
  title Container Diagram - Crunch Coordinator Node

  Person(operator, "Coordinator Operator", "Runs and configures the node")
  Person(cruncher, "Cruncher", "Builds model logic")

  System_Ext(feed, "Market Data Providers", "Pyth/Binance/MongoDB")
  System_Ext(protocol, "Crunch Protocol", "On-chain rewards/checkpoints")

  Container_Boundary(node, "Crunch Coordinator Node") {
    Container(feedWorker, "feed-data-worker", "Python asyncio worker", "Ingests and normalizes feed data")
    Container(predictWorker, "predict-worker", "Python asyncio worker", "Runs realtime/tournament prediction orchestration")
    Container(scoreWorker, "score-worker", "Python asyncio worker", "Resolves ground truth and scores predictions")
    Container(reportWorker, "report-worker", "FastAPI", "Reports, admin APIs, timing metrics")
    ContainerDb(postgres, "PostgreSQL", "SQLModel/JSONB", "Inputs, predictions, scores, snapshots, leaderboard, checkpoints")
    Container(modelOrch, "model-orchestrator", "model-orchestrator", "Runs competitor model containers")
    Container(reportUI, "report-ui", "Next.js", "Operator-facing web interface")
  }

  Rel(operator, reportUI, "Uses", "Browser/HTTP")
  Rel(reportUI, reportWorker, "Reads reports and health", "HTTP/JSON")

  Rel(feedWorker, feed, "Reads feed data", "HTTP/WebSocket/Polling")
  Rel(feedWorker, postgres, "Stores feed records", "SQL")

  Rel(predictWorker, postgres, "Reads configs and writes predictions", "SQL")
  Rel(predictWorker, modelOrch, "Tick + predict model calls", "gRPC")

  Rel(scoreWorker, postgres, "Reads predictions and writes scores/snapshots", "SQL")
  Rel(reportWorker, postgres, "Serves report/query endpoints", "SQL")

  Rel(reportWorker, protocol, "Publishes/records checkpoint state", "HTTP/JSON")
  Rel(cruncher, modelOrch, "Deploys models", "CLI/API")
```

## Notes

- `predict-worker` hosts the refactored predict architecture:
  - mode orchestration in concrete services
  - shared kernel/primitives in `predict_components.py`
- Prediction persistence remains a **critical write path**.
- Model metadata persistence is **non-critical** and deferred behind the critical path.
