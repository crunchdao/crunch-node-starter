---
name: coordinator-node-starter
description: Use when creating or customizing a Crunch coordinator node. Defines competition contracts, types, scoring, and the full pipeline from feed data to on-chain checkpoints.
---

# Coordinator Node Starter

Base template for Crunch coordinator nodes. Provides the engine (`coordinator-node` on PyPI) plus a customizable workspace with challenge package and node config.

## Project Structure

```
base/
  challenge/          # Participant-facing package (tracker, scoring, examples)
  node/
    config/           # crunch_config.py — single source of truth
    api/              # Custom FastAPI endpoints (auto-discovered)
    extensions/       # Node-side extensions (position manager, etc.)
    deployment/       # Docker config (model-orchestrator, report-ui)
coordinator_node/     # Engine (published to PyPI as coordinator-node)
tests/                # All tests
```

## Key Concepts

### CrunchConfig — Single Source of Truth

`base/node/config/crunch_config.py` defines all type shapes and behavior:
- 5 Pydantic types: `raw_input_type`, `input_type`, `output_type`, `ground_truth_type`, `score_type`
- `scheduled_predictions` — what to predict, how often, when to resolve
- `scoring_function` — if set, takes precedence over env var
- `aggregation` — windows, `value_field`, `ranking_key`
- `resolve_ground_truth`, `aggregate_snapshot`, `build_emission`

### Pipeline

```
Feed → Input (dumb log) → Prediction (owns resolution) → Score → Snapshot → Leaderboard → Checkpoint
```

### Scoring → Snapshots → Leaderboard

1. `scoring_function(prediction, ground_truth)` → ScoreRecord
2. `aggregate_snapshot([results])` → SnapshotRecord.result_summary
3. `_aggregate_from_snapshots()` → averages `value_field` per window, merges latest snapshot fields → leaderboard
4. `auto_report_schema()` → introspects `score_type` → auto-generates UI columns

### resolve_horizon_seconds

- `0` = immediate resolution. Ground truth from `InputRecord.raw_data`. For live trading.
- `> 0` = deferred. Score worker fetches feed window → `resolve_ground_truth(records)`.

## Commands

```bash
make fmt          # Format + lint fix
make test         # Lint + pytest
make deploy       # Docker build + start
make verify       # API + container checks
make verify-ui    # Browser UI checks
make verify-all   # Both
```

## Customization Flow

1. Define types in `base/node/config/crunch_config.py` (input, output, score)
2. Define `scheduled_predictions` (scope, interval, horizon)
3. Implement scoring function (stateless or stateful via `scoring_function` field)
4. Build challenge package (tracker interface, examples) in `base/challenge/`
5. Add node extensions in `base/node/extensions/` if needed
6. Run `make test`, `make deploy`, `make verify-all`

## Design Decisions (Current)

- **No backward compat**: No contracts.py, contract_loader, runtime_definitions
- **Input is dumb log**: InputRecord = id, raw_data, received_at. No status/actuals/scope.
- **Predictions own resolution**: PredictionRecord carries scope + resolvable_at
- **Type-safe JSONB**: Pydantic types ARE the parse/dump interface. No wrappers.
- **Aggregation.value_field**: Score field to read from snapshots for windows (fixes old ranking=0 bug)
- **CrunchConfig.scoring_function**: Stateful callables (e.g. PositionManager) supported
- **auto_report_schema**: Leaderboard columns auto-generated from score_type fields
