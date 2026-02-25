# Coordinator Node Documentation

- [Architecture Overview](./architecture.md) — system design, pipeline, workers
- [CrunchConfig](./crunch-config.md) — the single source of truth for all type shapes and behavior
- [Data Pipeline](./data-pipeline.md) — feed → predict → score → snapshot → leaderboard → checkpoint
- [Database Schema](./database-schema.md) — tables, JSONB fields, status lifecycles
- [Feed System](./feed-system.md) — data providers, subscriptions, backfill
- [Scoring & Aggregation](./scoring-and-aggregation.md) — scoring functions, snapshots, windows, leaderboard
- [Merkle Tamper Evidence](./merkle.md) — tamper-proof audit trail for scores and checkpoints
- [Report API](./report-api.md) — all REST endpoints served by the report worker
- [Scaffold Template](./scaffold.md) — how `crunch-cli init-workspace` creates new competitions
- [Deployment & Operations](./deployment.md) — Docker Compose, workers, env vars, monitoring
