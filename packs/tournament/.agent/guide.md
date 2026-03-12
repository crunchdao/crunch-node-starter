# Implementation Guide — Tournament Mode

How to build each component. Follow this order — later steps depend on earlier ones.

## Key Differences from Realtime

This is a **tournament** competition:
- No feed, no `scheduled_predictions`, no `resolve_horizon_seconds`
- Models receive a **batch of features** via `predict(features)` and return **per-sample predictions**
- Rounds are triggered via the tournament API, not by a polling loop
- Scoring is triggered explicitly after inference completes

## Starter Placeholders

The scaffold ships with working-but-meaningless values. **Confirm every one with the user:**

| Placeholder | Where |
|---|---|
| `RawInput.features: dict[str, float]` | CrunchConfig types |
| `InferenceOutput.predictions: list[dict]` | CrunchConfig types |
| `GroundTruth.target: float` | CrunchConfig types |
| `scoring: return 0.0` | `scoring.py` (stub) |
| `ranking_key: score_recent` | `Aggregation` |

## Use Tests as Implementation Targets

```bash
make test
```

Three test files track your progress:
- `test_tracker.py` — BaseModelClass behavior
- `test_examples.py` — example models match `InferenceOutput` contract
- `test_scoring.py` — scoring function correctness. Has `xfail` markers for stubs — remove after implementing real scoring.

## 1. Types and Tracker

**Types** — edit `node/config/crunch_config.py`:

All types are Pydantic models. The five types to override:
- `raw_input_type` — feature schema (what each sample looks like)
- `input_type` — what models receive (can transform from raw)
- `output_type` — what models return (per-sample prediction dict)
- `ground_truth_type` — actual outcome per sample (needs defaults!)
- `score_type` — what scoring produces

**IMPORTANT — all type fields MUST have defaults.** The score worker
dry-runs the scoring function at startup with empty instances.

**call_method** — tournament config uses:
```python
call_method = CallMethodConfig(
    method="predict",
    args=[CallMethodArg(name="features", type="JSON")],
)
```
This sends ONE feature dict as JSON to `model.predict(features)`. The engine
calls each model once per sample in the batch.

**Tracker** — edit `challenge/starter_challenge/cruncher.py`:
```python
class BaseModelClass:
    def predict(self, features: dict) -> dict:
        """Process a single feature sample and return a prediction.

        Args:
            features: Feature dict for one sample.

        Returns:
            {"prediction": float}  (or whatever InferenceOutput defines)
        """
        raise NotImplementedError
```

The model runner calls `predict(features)` once per sample.

## 2. Examples

Edit `challenge/starter_challenge/examples/`. Build ~3-5 simple models:
- Each receives `features: dict` (single sample) and returns a prediction dict
- **Diverse** — different strategies so they produce different scores
- **Contract-compliant** — return format matches `InferenceOutput`

```python
class MyModel(BaseModelClass):
    def predict(self, features: dict) -> dict:
        # your logic for this sample
        return {"prediction": some_value}
```

## 3. Ground Truth

For tournaments, ground truth is provided explicitly when scoring a round
(via `POST /tournament/rounds/{round_id}/score`). No feed-based resolution.

Define `GroundTruth` fields to match what your competition provides as actuals.

## 4. Scoring Function

Signature: `score_prediction(prediction: BaseModel, ground_truth: BaseModel) -> dict`

The scoring function receives typed Pydantic objects. Use attribute access
(e.g. `prediction.value`, `ground_truth.target`), not dict access.

The tournament service calls this **per sample** — each entry in `predictions`
is scored against the corresponding entry in the ground truth list. Results
are averaged across all samples.

**Receives:** `prediction` (typed `output_type` Pydantic instance), `ground_truth` (typed `ground_truth_type` Pydantic instance)
**Returns:** dict or Pydantic model matching `ScoreResult` — at minimum `{"value": float, "success": bool}`

## 5. Tournament API

No need to implement — endpoints are auto-discovered from `node/api/tournament.py`:

| Endpoint | Purpose |
|---|---|
| `POST /tournament/rounds/{round_id}/inference` | Send features, run all models |
| `POST /tournament/rounds/{round_id}/score` | Send ground truth, score round |
| `GET /tournament/rounds/{round_id}/status` | Check round state |

## 6. Deploy & Verify

```bash
make deploy          # Builds containers, runs validation
make verify-e2e      # Triggers a tournament round, checks scoring + leaderboard
```

The `verify-e2e` script:
1. Waits for the report worker to be healthy
2. Triggers inference via the tournament API
3. Triggers scoring with ground truth
4. Verifies scores propagated to the leaderboard

## No Feeds / No Scheduled Predictions

Tournament configs should have:
```python
scheduled_predictions: list = Field(default_factory=list)  # empty!
```

The `predict_service_class` in `.local.env` should be `TournamentPredictService`
(or the predict worker can be omitted entirely — inference is API-driven through
the report worker).
