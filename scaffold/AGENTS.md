# Coordinator Workspace — Agent Instructions

## Post-Deploy Monitoring (PRIMARY TASK)

**Your main job after any deploy is to ensure everything works end-to-end.**

After running `make deploy`:

1. **Run `make verify-e2e`** to confirm all services come up healthy.
2. **Continuously monitor for at least 20 minutes.** Re-run verification checks every few minutes — don't just check once and walk away.
3. **Cross-check data consistency** across all three layers on every pass:
   - **API** — hit every endpoint, confirm responses are well-formed and data is fresh.
   - **Database** — query the DB directly, confirm records match what the API returns (counts, latest timestamps, scores, leaderboard rankings).
   - **Website (UI)** — check browser UI pages to confirm they render the same data the API and DB show.
4. **If anything is inconsistent or broken, fix it immediately.** Diagnose via docker logs, DB queries, and API responses. Apply code fixes, redeploy if needed, and restart the monitoring clock.
5. **Only declare success after 20 minutes of all-green, consistent results** across API, DB, and UI with no errors in docker logs.

**Checklist per monitoring pass:**
- [ ] All docker containers running (`docker ps`)
- [ ] No errors/tracebacks in docker logs
- [ ] API endpoints return correct, fresh data
- [ ] DB records match API responses (counts, values, timestamps)
- [ ] UI pages render and show data consistent with API/DB
- [ ] Pipeline flow working: predictions → scores → snapshots → leaderboard

## Commands

From workspace root (proxies to `node/`):

| Command | Purpose |
|---------|---------|
| `make deploy` | Validate config → build → start all services |
| `make preflight` | deploy → check-models → verify-e2e (full) |
| `make verify-e2e` | API + container + pipeline checks |
| `make check-models` | Verify model runners are healthy |
| `make validate` | Pre-deploy config validation (no Docker) |
| `make logs` | Stream all service logs |
| `make down` | Tear down all containers |
| `make init-db` | Initialize database |
| `make reset-db` | Reset database |
| `make starter` | Switch to starter UI |
| `make platform` | Switch to platform UI |
| `make backfill` | Backfill historical feed data |
| `make test` | Run challenge unit tests |

## Troubleshooting

### Ports already in use
Preflight will halt if required ports are busy. Inspect:
```bash
lsof -nP -iTCP:3000 -sTCP:LISTEN   # report-ui
lsof -nP -iTCP:8000 -sTCP:LISTEN   # report-worker
lsof -nP -iTCP:9091 -sTCP:LISTEN   # model-orchestrator
lsof -nP -iTCP:5432 -sTCP:LISTEN   # postgres
```

### BAD_IMPLEMENTATION / model runner failures
- Confirm `MODEL_BASE_CLASSNAME=tracker.TrackerBase` in `node/.local.env`
- Ensure challenge package path is wired in `pyproject.toml` under `[tool.uv.sources]`

### NEXT_PUBLIC_API_URL must be Docker-internal
The UI's Next.js `rewrites()` proxy runs server-side inside Docker. Never set to `localhost`.
- ✅ `http://report-worker:8000` (Docker DNS)
- ❌ `http://localhost:8000` → ECONNREFUSED inside container

### Clean reset
```bash
make down
rm -rf .venv
make deploy
make verify-e2e
```

## Workers

| Container | Purpose |
|---|---|
| `feed-data-worker` | Ingests feed data (Pyth, Binance) |
| `predict-worker` | Event-driven: feed → models → predictions |
| `score-worker` | Resolves actuals → scores → snapshots → leaderboard |
| `checkpoint-worker` | Aggregates snapshots → EmissionCheckpoint |
| `report-worker` | FastAPI serving all report endpoints |

## Report API

| Endpoint | Description |
|---|---|
| `GET /healthz` | Health check |
| `GET /info` | Node identity (crunch_id, address, network) |
| `GET /reports/schema` | Auto-generated report schema |
| `GET /reports/models` | Registered models |
| `GET /reports/leaderboard` | Current leaderboard |
| `GET /reports/models/global` | Per-model windowed scores |
| `GET /reports/models/params` | Scores grouped by scope |
| `GET /reports/models/metrics` | Metrics timeseries |
| `GET /reports/models/summary` | Latest snapshot per model |
| `GET /reports/predictions` | Prediction history |
| `GET /reports/feeds` | Active feed subscriptions |
| `GET /reports/feeds/tail` | Latest feed records |
| `GET /reports/snapshots` | Per-model period summaries |
| `GET /reports/checkpoints` | Checkpoint history |
| `GET /reports/diversity` | Model diversity overview |
| `GET /reports/ensemble/history` | Ensemble performance over time |
| `GET /reports/merkle/cycles` | Merkle tamper evidence |

## Key Architecture

### Pipeline
```
Feed → Input (dumb log) → Prediction (owns resolution) → Score → Snapshot → Leaderboard → Checkpoint
```

### resolve_horizon_seconds
- `0` = immediate resolution (live trading). Ground truth from `InputRecord.raw_data`.
- `> 0` = deferred. Must exceed feed data interval, otherwise no feed records exist for scoring.

### Aggregation
- `value_field` = score field to average in windows (default `"value"`)
- `ranking_key` = which metric to rank by (can be window name or score field)

### CrunchConfig.scoring_function
- If set, takes precedence over `SCORING_FUNCTION` env var
- Enables stateful scoring (e.g. PositionManager-backed trading)

### Where to edit

| What to change | Where |
|---|---|
| Competition types, scoring, schedules | `node/config/crunch_config.py` |
| Challenge behavior (tracker, examples) | `challenge/starter_challenge/` |
| Node env config | `node/.local.env` |
| Custom API endpoints | `node/api/` |
| Node-side extensions | `node/extensions/` |
| Deployment assets (orchestrator, UI) | `node/deployment/` |

### Folder layout
```
├── node/          ← docker-compose, workers, config (uses coordinator-node from PyPI)
│   ├── config/    ← crunch_config.py — all type shapes, callables, schedules
│   ├── api/       ← custom FastAPI endpoints (auto-discovered)
│   ├── extensions/← node-side extensions (position manager, etc.)
│   ├── deployment/← model-orchestrator, report-ui config
│   └── scripts/   ← verify, backfill, validate utilities
├── challenge/     ← participant-facing package (tracker, scoring, examples)
└── Makefile       ← proxies to node/
```
