# Architecture — Tournament Mode

## Pipeline

```
API Request → Input → Prediction → Score → Snapshot → Leaderboard → Checkpoint
```

No feed, no polling. Rounds are triggered via the tournament API.

## Workers

| Worker | Role |
|---|---|
| `predict-worker` | Stays alive for model runner sync. No prediction loop. |
| `score-worker` | Processes scores → snapshots → leaderboard → checkpoints |
| `report-worker` | FastAPI server — hosts tournament API + report endpoints |
| `model-orchestrator` | Builds + manages model containers (gRPC) |

## CrunchConfig (`node/config/crunch_config.py`)

Single source of truth for competition behavior:

| Field | Purpose |
|---|---|
| `raw_input_type` | Feature schema (what each sample looks like) |
| `input_type` | What models receive (can transform from raw) |
| `output_type` | What models return — **must have `predictions: list[dict]`** |
| `ground_truth_type` | Actual outcome per sample |
| `score_type` | What scoring produces |
| `call_method` | **Tournament: `predict(features: JSON)`** — one arg, not three |
| `scheduled_predictions` | **Empty list** — rounds are API-driven |
| `scoring_function` | Per-sample scoring. Called once per (prediction, ground_truth) pair. |
| `aggregation.value_field` | Score field to average in windows |
| `aggregation.ranking_key` | Metric that ranks the leaderboard |

## Tournament Model Contract

Models extend `TrackerBase` and implement:

```python
def predict(self, features: dict) -> dict:
    """Process a single feature sample and return a prediction."""
    return {"prediction": compute(features)}
```

The engine calls `predict(features)` once per sample in the batch. Each call
receives ONE feature dict (not the full batch).

## Scoring Flow (Tournament)

1. `POST /tournament/rounds/{round_id}/inference` → engine loops over features,
   calls each model once per sample → N × M predictions saved
2. `POST /tournament/rounds/{round_id}/score` → predictions grouped by model,
   sorted by feature_index, scored 1:1 against GT items:
   - `scoring_function(prediction_output, ground_truth_item)` per pair
   - One ScoreRecord per prediction
3. Score worker picks up scores → snapshots → leaderboard

## Tournament API

| Endpoint | Purpose |
|---|---|
| `POST /tournament/rounds/{round_id}/inference` | Upload features, run all models |
| `POST /tournament/rounds/{round_id}/score` | Upload ground truth, score predictions |
| `GET /tournament/rounds/{round_id}/status` | Check round state (not_found, inference_complete, scored) |

## Report API

Same as base scaffold — `/reports/leaderboard`, `/reports/models`, etc.

## Status Lifecycles

```
Input:       Saved once (features batch + round_id)
Prediction:  PENDING → SCORED / FAILED / ABSENT
```

## Gotchas

### NEXT_PUBLIC_API_URL must use Docker DNS
- ✅ `http://report-worker:8000`
- ❌ `http://localhost:8000`

### InferenceOutput is per-sample
Tournament models return a single prediction dict (e.g. `{"prediction": float}`).
The engine calls each model once per feature sample.

### All scores zero?
- Scoring stub not replaced
- `predictions` list empty or wrong format
- Ground truth fields don't match what scoring function reads

### Models not found?
Model containers build on first deploy. Wait 30-60s after deploy before
calling inference. Use `check_models.py` to verify readiness.

### Leaderboard rankings all zero?
`aggregation.value_field` doesn't match any field in `score_type`.

### Clean reset
```bash
make down && docker volume rm $(docker volume ls -q | grep crunch) && make deploy
```
