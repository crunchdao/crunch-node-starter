# Coordinator Node — Agent Instructions

Base template for Crunch coordinator nodes. Provides the engine (`coordinator-node` on PyPI) plus a customizable workspace with challenge package and node config.

## Code Formatting (MANDATORY)

This project uses **Ruff** for formatting and linting all Python code.

**Before every commit, run:**
```bash
make fmt
```

**Rules:**
- Always run `make fmt` after editing Python files — no exceptions.
- Never disable or skip ruff rules without explicit user approval.
- Imports are sorted automatically (isort via ruff).
- Line length is 88 characters (ruff formatter handles wrapping).

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

## Key Architecture

### CrunchConfig — Single Source of Truth

`base/node/config/crunch_config.py` defines all type shapes and behavior:
- 5 Pydantic types: `raw_input_type`, `input_type`, `output_type`, `ground_truth_type`, `score_type`
- `scheduled_predictions` — what to predict, how often, when to resolve
- `scoring_function` — if set, takes precedence over `SCORING_FUNCTION` env var
- `aggregation` — windows, `value_field`, `ranking_key`
- `resolve_ground_truth`, `aggregate_snapshot`, `build_emission`
- Config loading: `coordinator_node.config_loader.load_config()` — no contracts.py, no contract_loader
- Type-safe JSONB: Pydantic types ARE the parse/dump interface. No wrappers.
- Input is a dumb log: `InputRecord` = `id`, `raw_data`, `received_at` — saved once, never updated
- Predictions own resolution: `PredictionRecord` carries `scope`, `resolvable_at`
- `Aggregation.ranking_key`: which metric to rank by (can be window name or score field)

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

| Command | Purpose |
|---------|---------|
| `make fmt` | Auto-format and auto-fix all Python files |
| `make lint` | Check formatting and linting (no changes) |
| `make check` | Lint + tests |
| `make test` | Runs lint then pytest |
| `make deploy` | Docker build + start |
| `make verify` | API + container checks (headless) |
| `make verify-ui` | Browser-based UI page checks (needs agent-browser) |
| `make verify-all` | Both verify + verify-ui |

## Testing

```bash
make test
```

Tests live in `tests/`. PYTHONPATH includes `base/challenge` and `base/node`.

## Deployment Verification

After deploying (`make deploy`), verify the system is working:

```bash
make verify       # API + container checks (headless)
make verify-ui    # Browser-based UI page checks (needs agent-browser)
make verify-all   # Both
```

`make verify` checks all docker containers, hits every API endpoint, verifies
data pipeline flow (predictions → scores → snapshots → leaderboard), scans
docker logs for errors, and checks UI reachability.

## Customization Flow

1. Define types in `base/node/config/crunch_config.py` (input, output, score)
2. Define `scheduled_predictions` (scope, interval, horizon)
3. Implement scoring function (stateless or stateful via `scoring_function` field)
4. Build challenge package (tracker interface, examples) in `base/challenge/`
5. Add node extensions in `base/node/extensions/` if needed
6. Run `make test`, `make deploy`, `make verify-all`

## Design Decisions

- **No backward compat**: No contracts.py, contract_loader, runtime_definitions
- **Input is dumb log**: InputRecord = id, raw_data, received_at. No status/actuals/scope.
- **Predictions own resolution**: PredictionRecord carries scope + resolvable_at
- **Type-safe JSONB**: Pydantic types ARE the parse/dump interface. No wrappers.
- **Aggregation.value_field**: Score field to read from snapshots for windows (fixes old ranking=0 bug)
- **CrunchConfig.scoring_function**: Stateful callables (e.g. PositionManager) supported
- **auto_report_schema**: Leaderboard columns auto-generated from score_type fields

## PyPI Publishing

```bash
uv build
uv publish --token "$(grep password ~/.pypirc | head -1 | awk '{print $3}')"
```
