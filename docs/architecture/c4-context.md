# C4 Level 1 — System Context (Crunch Coordinator Node)

```mermaid
C4Context
  title System Context - Crunch Coordinator Node

  Person(cruncher, "Cruncher", "Builds and deploys prediction models")
  Person(operator, "Coordinator Operator", "Configures and runs the coordinator")

  System(coord, "Crunch Coordinator Node", "Runs feed ingestion, prediction, scoring, reporting, and checkpoint preparation")

  System_Ext(feed, "Market Data Providers", "Pyth, Binance, MongoDB feeds")
  System_Ext(orchestrator, "Model Orchestrator", "Hosts and executes participant models")
  System_Ext(protocol, "Crunch Protocol", "On-chain checkpoint confirmation and rewards")

  Rel(operator, coord, "Configures and operates", "Docker / env / CrunchConfig")
  Rel(cruncher, orchestrator, "Deploys models to", "Model runner")

  Rel(coord, feed, "Fetches/streams market data from", "HTTP/WebSocket/Polling")
  Rel(coord, orchestrator, "Ticks models and requests predictions", "gRPC")
  Rel(coord, protocol, "Produces checkpoint payloads for", "EmissionCheckpoint JSON")
```

## Scope

This diagram shows the coordinator node as a single system and its external
actors/systems. Internally, the node now uses a **predict kernel architecture**
that separates:

- **Mode-specific orchestration** (realtime/tournament flows)
- **Shared prediction primitives** (runner lifecycle, encoding, validation, record building)

The architecture target remains compatibility with **~50ms predict roundtrip**
(optimized path), while ensuring non-critical metadata persistence does not
block prediction flow.
