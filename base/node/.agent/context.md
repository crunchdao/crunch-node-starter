# Node Context — starter-challenge

## What this is

Standalone node runtime workspace. Contains docker-compose, workers, config, and the report API. Runs the `coordinator-node` engine from PyPI.

## Primary commands

```bash
make deploy                                                    # Build and start all services
make verify-e2e                                                # End-to-end validation
make logs                                                      # Stream all service logs
make logs-capture                                              # Write structured logs to runtime-services.jsonl
make down                                                      # Tear down all services
make backfill SOURCE=pyth SUBJECT=BTC FROM=2026-01-01 TO=2026-02-01  # Backfill historical data
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
| `http://localhost:8000/healthz` | Health check |
| `http://localhost:8000/reports/models` | Registered models |
| `http://localhost:8000/reports/leaderboard` | Current leaderboard |
| `http://localhost:8000/reports/predictions` | Prediction history |
| `http://localhost:8000/reports/feeds` | Active feed subscriptions |
| `http://localhost:8000/reports/snapshots` | Per-model period summaries (enriched with metrics) |
| `http://localhost:8000/reports/checkpoints` | Checkpoint history |
| `http://localhost:8000/reports/emissions/latest` | Latest emission |
| `http://localhost:8000/reports/checkpoints/{id}/emission` | Raw emission (frac64) |
| `http://localhost:8000/reports/checkpoints/{id}/emission/cli-format` | Coordinator-CLI JSON format |

## API Security

Set `API_KEY` in `.local.env` to enable authentication.

- **Admin endpoints** (backfill, checkpoints, `/custom/*`) always require the key when set
- **Public endpoints** (leaderboard, schema, models) stay open
- **Read endpoints** optionally gated via `API_READ_AUTH=true`

## Custom API endpoints

Drop `.py` files in `api/` with a `router = APIRouter()`. Auto-mounted at report-worker startup. Full DB access via `Depends`.

Config: `API_ROUTES_DIR` (default `api/`), `API_ROUTES` (explicit `module:attr` paths).

## Folder map — where to put things

| Folder | Purpose | When to use |
|---|---|---|
| `api/` | Custom FastAPI endpoints | Add any `.py` file with `router = APIRouter()` — auto-discovered at startup. See `api/README.md` for examples with DB access and metrics. |
| `extensions/` | Node-specific callable overrides | Edge-case Python modules needed by the runtime (custom feed providers, specialized scoring helpers). Most customization should go in `runtime_definitions/crunch_config.py` instead. |
| `plugins/` | Node-side integrations | Custom feed providers beyond built-in Pyth/Binance, external API integrations, data enrichment. Use when code needs secrets or calls private APIs that shouldn't be in the challenge package. |
| `runtime_definitions/` | Competition contract | `crunch_config.py` is the primary file — defines all type shapes, callables, and behavior. `contracts.py` is backward compat. |
| `config/` | Runtime configuration | `callables.env` for scoring function path, `scheduled_prediction_configs.json` for prediction schedule and scope. |
| `deployment/` | Local deployment assets | `model-orchestrator-local/` for local model runner config, `report-ui/` for dashboard settings. |
| `scripts/` | Utility scripts (do not edit) | `verify_e2e.py`, `backfill.py`, `check_models.py`, `capture_runtime_logs.py` — called by Makefile targets. |

## Edit boundaries

| What | Where |
|---|---|
| Node env config | `.local.env`, `.env` |
| Callable paths | `config/callables.env` |
| Prediction schedules | `config/scheduled_prediction_configs.json` — **`resolve_after_seconds` must be > feed data interval** (see below) |
| Competition types & behavior | `runtime_definitions/crunch_config.py` (preferred), `runtime_definitions/contracts.py` (backward compat) |
| Custom API endpoints | `api/` |
| Custom callable modules | `extensions/` |
| External integrations / feed providers | `plugins/` |
| Local deployment config | `deployment/` |
| Challenge implementation | Mounted from `../challenge` |

## ⚠️ Starter placeholder values

All values in `config/`, `.local.env`, `runtime_definitions/crunch_config.py`,
and `scheduled_prediction_configs.json` are starter placeholders (BTC, 60s
horizon, 1s granularity, etc.). They exist to make the scaffold bootable.
**Ask the user for every competition-specific value before customizing.**
See `../.agent/playbooks/customize.md` for the full placeholder table.

## Prediction schedule constraint

`resolve_after_seconds` in `config/scheduled_prediction_configs.json` controls how long the score-worker waits before fetching ground truth from the feed. **It must be strictly greater than the feed's effective data interval**, otherwise no feed data will exist yet when scoring runs, and all predictions fail to score silently.

- Feed granularity `1s` + poll every `5s` → `resolve_after_seconds` > 5
- Feed granularity `1m` → `resolve_after_seconds` > 60
- Feed granularity `5m` → `resolve_after_seconds` > 300

Always ask the user what `resolve_after_seconds` should be — do not assume a default.

## ⛔ Known gotchas

### 1. NEXT_PUBLIC_API_URL must be Docker-internal
`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_API_URL_MODEL_ORCHESTRATOR` are used by
the Next.js `rewrites()` proxy **server-side inside Docker**. The browser calls
`/api/*` on the UI port and Next.js proxies to the backend. **Never set these
to `localhost`** — the SSR server runs inside Docker where `localhost` is itself.

- ✅ `http://report-worker:8000` (Docker DNS)
- ✅ Leave unset (docker-compose.yml defaults are correct)
- ❌ `http://localhost:8000` → ECONNREFUSED inside the container

### 2. resolve_after_seconds must exceed feed granularity
The score-worker fetches feed records in a time window of `resolve_after_seconds`.
If this window is shorter than the feed granularity, it contains zero records
and predictions silently fail to score.

- Feed `1m` → `resolve_after_seconds` >= 75
- Feed `1s` → `resolve_after_seconds` >= 10

### 3. Model submissions must be self-contained
Model-runner containers do NOT have the challenge package installed. Any
`from <challenge_pkg>.X import Y` in a submission will crash with
`ModuleNotFoundError`. Use inline classes or import from local `tracker.py` only.

### 4. score_prediction receives InferenceOutput, not the full prediction
The score-worker calls `score_fn(typed_output, actuals)` where `typed_output`
is the coerced InferenceOutput dict (e.g. `{action, trade_pair, leverage}`),
NOT the full prediction row. Don't look for `portfolio_snapshot` or other
enriched fields — compute the score from the output + ground truth directly.

### 5. check-models must tolerate partial failures
Some models may fail (bad imports, missing deps) while others run fine.
Only fail the pipeline if ZERO models reach RUNNING.

## Pre-deploy validation

`make validate` checks all 5 gotchas above without Docker. Runs automatically
as part of `make deploy`. Use `make preflight` for the full gate:
validate → deploy → check-models → verify-e2e.

## Logs and artifacts

- `make logs` streams all service logs from docker compose
- `make logs-capture` writes structured logs to `runtime-services.jsonl`
- Known failure modes and recovery: `RUNBOOK.md`
