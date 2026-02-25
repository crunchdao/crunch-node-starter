# Coordinator Node — Agent Instructions

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

**Commands:**
| Command | Purpose |
|---------|---------|
| `make fmt` | Auto-format and auto-fix all Python files |
| `make lint` | Check formatting and linting (no changes) |
| `make check` | Lint + tests |
| `make test` | Runs lint then pytest |

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

## Key Architecture

- **Single source of truth**: `base/node/config/crunch_config.py` — `CrunchConfig` subclass
- **Config loading**: `coordinator_node.config_loader.load_config()` — no contracts.py, no contract_loader
- **Type-safe JSONB**: 5 Pydantic types on CrunchConfig define every data boundary
- **Input is a dumb log**: `InputRecord` = `id`, `raw_data`, `received_at` — saved once, never updated
- **Predictions own resolution**: `PredictionRecord` carries `scope`, `resolvable_at`
- **`resolve_horizon_seconds=0`**: immediate resolution — ground truth from `InputRecord.raw_data`
- **`Aggregation.value_field`**: score field to average in windows (default `"value"`)
- **`Aggregation.ranking_key`**: which metric to rank by (can be window name or score field)
- **`CrunchConfig.scoring_function`**: if set, takes precedence over `SCORING_FUNCTION` env var

## PyPI Publishing

```bash
uv build
uv publish --token "$(grep password ~/.pypirc | head -1 | awk '{print $3}')"
```
