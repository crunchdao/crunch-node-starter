# Coordinator Node — Agent Instructions

Base template for Crunch coordinator nodes. Provides the engine (`crunch-node` on PyPI) plus a customizable workspace with challenge package and node config.

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
scaffold/
  challenge/          # Participant-facing package (tracker, scoring, examples)
  node/
    config/           # crunch_config.py — single source of truth
    api/              # Custom FastAPI endpoints (auto-discovered)
    extensions/       # Node-side extensions (position manager, etc.)
    deployment/       # Docker config (model-orchestrator, report-ui)
  webapp/             # Cloned from crunchdao/coordinator-webapp at scaffold init
crunch_node/          # Engine (published to PyPI as crunch-node)
tests/                # All tests
```

Scaffolded workspaces build `report-ui` from the local `webapp/` clone via
`node/.local.env` (`REPORT_UI_BUILD_CONTEXT=../webapp`).

## Backend → UI Extension Workflow (MANDATORY)

When a request includes extending `webapp/` for node/backend features, use one
coordination model only: **node-first contract**.

Before any UI code edits, collect both:
1. **Backend source of truth** (`PR`, commit, branch, or endpoint spec)
2. **UI success criteria** (what should appear where, expected states/workflows)

If either is missing, **stop implementation and ask**. Do not proceed with
assumption-based UI edits.

Then create/update `docs/ui-contracts/<topic>.md` with:
- endpoint and payload mapping
- loading/empty/error states
- acceptance criteria tied to backend behavior

Implement UI changes from this contract, then validate against the acceptance
criteria.

## Key Architecture

### CrunchConfig — Single Source of Truth

`scaffold/node/config/crunch_config.py` defines all type shapes and behavior:
- 5 Pydantic types: `raw_input_type`, `input_type`, `output_type`, `ground_truth_type`, `score_type`
- `scheduled_predictions` — what to predict, how often, when to resolve
- `scoring_function` — if set, takes precedence over `SCORING_FUNCTION` env var
- `aggregation` — windows, `value_field`, `ranking_key`
- `resolve_ground_truth`, `aggregate_snapshot`, `build_emission`
- Config loading: `crunch_node.config_loader.load_config()` — no contracts.py, no contract_loader
- Type-safe JSONB: Pydantic types ARE the parse/dump interface. No wrappers.
- Input is a dumb log: `InputRecord` = `id`, `raw_data`, `received_at` — saved once, never updated
- Predictions own resolution: `PredictionRecord` carries `scope`, `resolvable_at`
- `Aggregation.ranking_key`: which metric to rank by (can be window name or score field)

### Pipeline

```
Feed → Input (dumb log) → Prediction (owns resolution) → Score → Snapshot → Leaderboard → Checkpoint
```

### Predict Latency Budget (MANDATORY)

- Treat **~50ms predict roundtrip** as an architecture target (when optimized).
- "Predict roundtrip" = predict worker path from data availability/wakeup to persisted prediction records.
- If a design/architecture decision is expected to push latency materially above this target, **explicitly notify the user**.
- Always include: (1) why deviation is needed, (2) estimated latency impact, (3) mitigation alternatives.

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

Tests live in `tests/`. PYTHONPATH includes `scaffold/challenge` and `scaffold/node`.

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

1. Define types in `scaffold/node/config/crunch_config.py` (input, output, score)
2. Define `scheduled_predictions` (scope, interval, horizon)
3. Implement scoring function (stateless or stateful via `scoring_function` field)
4. Build challenge package (tracker interface, examples) in `scaffold/challenge/`
5. Add node extensions in `scaffold/node/extensions/` if needed
6. Run `make test`, `make deploy`, `make verify-all`

## Design Decisions

- **No backward compat**: No contracts.py, contract_loader, runtime_definitions
- **Input is dumb log**: InputRecord = id, raw_data, received_at. No status/actuals/scope.
- **Predictions own resolution**: PredictionRecord carries scope + resolvable_at
- **Type-safe JSONB**: Pydantic types ARE the parse/dump interface. No wrappers.
- **Aggregation.value_field**: Score field to read from snapshots for windows (fixes old ranking=0 bug)
- **CrunchConfig.scoring_function**: Stateful callables (e.g. PositionManager) supported
- **auto_report_schema**: Leaderboard columns auto-generated from score_type fields

## Benchmarking

The benchmark (`tests/benchmark/`) gives an agent the scaffold workspace and a spec (build a BTC direction competition), then verifies 8 milestones: types, ground truth, scoring, examples, tests, deploy, e2e, and metrics collection.

**Benchmark Milestones:**
1. **types_correct** — InferenceOutput has direction:str and confidence:float
2. **ground_truth_type** — GroundTruth type has profit:float and direction_up:bool with defaults
3. **scoring_implemented** — Scoring function works correctly with test cases
4. **examples_exist** — Required example tracker files exist with predict() methods
5. **tests_pass** — `make test` passes
6. **deploy_succeeded** — Docker containers are running
7. **e2e_verified** — `make verify-e2e` passes (full pipeline working)
8. **metrics_collection_verified** — `/timing-metrics` endpoint shows pipeline metrics are collected

**Run a benchmark:**
```bash
# Default (pi, 15min timeout, standard evidence)
python -m tests.benchmark.run_benchmark

# With a specific model and timeout
python -m tests.benchmark.run_benchmark --agent-cmd "pi --model claude-opus-4-6" --timeout 180

# With Claude Code instead of pi
python -m tests.benchmark.run_benchmark --agent-cmd claude --timeout 600
```

**Key options:**
| Flag | Default | Purpose |
|------|---------|---------|
| `--agent-cmd` | `pi` | Agent CLI command (extra flags like `--model` are preserved) |
| `--timeout` | `900` (15min) | Kill the agent after this many seconds |
| `--evidence` | `standard` | `fast` (milestones only), `standard` (+session), `full` (+screenshots) |
| `--workspace` | temp dir | Use a fixed directory (useful for debugging) |
| `--verify-only DIR` | — | Skip agent, just verify an existing workspace |
| `--compare` | — | Compare last two results (no run) |

**Outputs:**
- `tests/benchmark/results/<timestamp>.json` — milestone results + token/cost stats
- `tests/benchmark/logs/<timestamp>.log` — agent stdout/stderr
- `tests/benchmark/logs/<timestamp>-session.jsonl` — pi session file
- `tests/benchmark/logs/<timestamp>-evidence/session.html` — browsable session export

**View session HTML:**
```bash
open tests/benchmark/logs/<timestamp>-evidence/session.html
```

**Compare last two runs:**
```bash
python -m tests.benchmark.run_benchmark --compare
```

## PyPI Publishing

```bash
uv build
uv publish --token "$(grep password ~/.pypirc | head -1 | awk '{print $3}')"
```
