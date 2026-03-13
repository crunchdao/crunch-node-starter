"""Tournament benchmark spec — property-price-prediction-v1.

Contains the prompt given to the agent and expected values
used by verify.py to check milestones.
"""

from __future__ import annotations

SPEC_VERSION = "property-price-prediction-v1"

AGENT_PROMPT = """\
Build a property price prediction tournament from this scaffold workspace.

Read .agents/guide.md to understand the architecture. Then implement ALL code
changes FIRST, run `make test`, and ONLY THEN deploy.

CRITICAL TIME MANAGEMENT:
- Do NOT run `make deploy` or `make verify-e2e` until ALL code changes are done
- Do NOT verify the baseline scaffold — it works, just start implementing
- Do NOT run `make logs` — it follows logs forever and wastes your time budget
- Do NOT use `sleep` commands — `make verify-e2e` polls internally
- Implement everything, run `make test`, THEN deploy and verify

This is a TOURNAMENT competition, not a realtime one. The key difference:

- No streaming feed. No ticking. No resolve_horizon_seconds.
- The tournament engine calls each model once PER PROPERTY. Each call
  receives one feature dict and returns one prediction dict.
- Ground truth (actual sale prices) arrives separately after inference.
- Two API endpoints drive rounds: one for inference, one for scoring.

The base package already provides:
- `crunch_node.services.tournament_predict.TournamentPredictService`
- A scaffold tournament API in `scaffold/node/api/tournament.py`

Here is the exact specification:

## Types (edit node/config/crunch_config.py)

Replace ALL default types. This is NOT a BTC prediction challenge.

InferenceInput — what models receive (property features):
- living_area_sqft: float = 0.0
- lot_size_sqft: float = 0.0
- bedrooms: int = 0
- bathrooms: float = 0.0
- year_built: int = 2000
- latitude: float = 0.0
- longitude: float = 0.0
- has_garage: bool = False
- has_pool: bool = False

InferenceOutput — what models return:
- predicted_price: float = 0.0

GroundTruth — actual sale prices:
- price: float = 0.0

ScoreResult — what scoring produces:
- value: float = 0.0  (this is 1 - MAPE, the primary ranking metric)
- mape: float = 0.0
- mae: float = 0.0
- success: bool = True
- failed_reason: str | None = None

IMPORTANT: Set predict_service_class in CrunchConfig:

    from crunch_node.services.tournament_predict import TournamentPredictService
    predict_service_class: type | None = TournamentPredictService

IMPORTANT: Remove scheduled_predictions entirely or set to empty list [].
Tournament competitions don't use scheduled predictions.

IMPORTANT: Set call_method to use JSON for the features batch:

    from crunch_node.crunch_config import CallMethodConfig, CallMethodArg
    call_method: CallMethodConfig = CallMethodConfig(
        method="predict",
        args=[CallMethodArg(name="features", type="JSON")],
    )

## Scoring (edit challenge/starter_challenge/scoring.py)

score_prediction(prediction, ground_truth) -> dict:
- Extract predicted_price from prediction
- Extract actual price from ground_truth
- Calculate MAPE: abs(predicted - actual) / max(abs(actual), 1e-9)
- Calculate MAE: abs(predicted - actual)
- score = max(0.0, 1.0 - mape)
- Return {"value": score, "mape": mape, "mae": mae, "success": True, "failed_reason": None}

Wire this in CrunchConfig:
    scoring_function = staticmethod(score_prediction)

## Ground Truth

Do NOT set resolve_ground_truth — tournaments don't use it.
Ground truth comes via the /tournament/rounds/{round_id}/score endpoint.

## Examples (edit challenge/starter_challenge/examples/)

Create exactly 2 example trackers:

1. median_price_tracker.py — MedianPriceTracker
   - Always predicts $350,000 (national median)
   - predict(features) receives one property's feature dict
   - Returns {"predicted_price": 350000.0}

2. sqft_price_tracker.py — SqftPriceTracker
   - Predicts based on square footage: price = living_area_sqft * 200
   - predict(features) receives one property's feature dict
   - Returns {"predicted_price": features["living_area_sqft"] * 200}

All trackers extend TrackerBase. predict(features) receives a single feature
dict and returns a single prediction dict. The engine calls predict() once
per property in the batch.

## Test Data (create challenge/starter_challenge/data/)

Create two JSON files with synthetic property data:

1. in_sample.json — 20 properties for training/testing
2. out_of_sample.json — 10 properties for validation

Each property should have ALL InferenceInput fields plus a "price" field (ground truth).
Use realistic US property data:
- Prices: $150,000 to $1,500,000
- Living area: 800 to 4000 sqft
- Bedrooms: 1 to 6
- Bathrooms: 1.0 to 4.0
- Year built: 1950 to 2024
- Latitude/longitude: within US bounds
- Mix of has_garage/has_pool true/false

## Tests

- Update test_scoring.py: test MAPE scoring with property prices
- Update test_examples.py if needed for new output shape
- make test must pass

## Deploy & Verify

ONLY after all code changes are done and `make test` passes:
- Run `make deploy`
- If port conflicts: run `make down`, then `docker rm -f $(docker ps -aq --filter name=crunch-node-) 2>/dev/null || true`, then retry
- Run `make verify-e2e` immediately after deploy
- If verify fails, check container logs with `docker compose -f docker-compose.yml --env-file .local.env logs --tail=50 <service>` (do NOT use `make logs`)
- Fix and retry
"""

# --- Expected values for milestone verification ---

EXPECTED_OUTPUT_FIELDS = {
    "predicted_price": "float",
}

EXPECTED_INPUT_FIELDS = {
    "living_area_sqft": "float",
    "lot_size_sqft": "float",
    "bedrooms": "int",
    "bathrooms": "float",
    "year_built": "int",
    "latitude": "float",
    "longitude": "float",
    "has_garage": "bool",
    "has_pool": "bool",
}

EXPECTED_SCORE_FIELDS = {
    "value": "float",
    "mape": "float",
    "mae": "float",
    "success": "bool",
    "failed_reason": ("str", "None"),
}

EXPECTED_GROUND_TRUTH_FIELDS = {
    "price": "float",
}

EXPECTED_EXAMPLES = [
    "median_price_tracker.py",
    "sqft_price_tracker.py",
]

EXPECTED_EXAMPLE_CLASSES = [
    "MedianPriceTracker",
    "SqftPriceTracker",
]

# Scoring test cases: (prediction, ground_truth, expected_score_range)
SCORING_TEST_CASES = [
    # Perfect prediction
    (
        {"predicted_price": 500000.0},
        {"price": 500000.0},
        (0.99, 1.01),  # score ≈ 1.0
    ),
    # 10% off → score ≈ 0.9
    (
        {"predicted_price": 450000.0},
        {"price": 500000.0},
        (0.85, 0.95),
    ),
    # 50% off → score ≈ 0.5
    (
        {"predicted_price": 250000.0},
        {"price": 500000.0},
        (0.45, 0.55),
    ),
    # Way off → score near 0
    (
        {"predicted_price": 50000.0},
        {"price": 500000.0},
        (-0.01, 0.15),
    ),
]

# Test data: synthetic properties for in/out of sample
SAMPLE_PROPERTIES = [
    {
        "living_area_sqft": 2000.0,
        "lot_size_sqft": 5000.0,
        "bedrooms": 3,
        "bathrooms": 2.0,
        "year_built": 2005,
        "latitude": 37.77,
        "longitude": -122.42,
        "has_garage": True,
        "has_pool": False,
        "price": 850000.0,
    },
    {
        "living_area_sqft": 1200.0,
        "lot_size_sqft": 3000.0,
        "bedrooms": 2,
        "bathrooms": 1.0,
        "year_built": 1975,
        "latitude": 33.94,
        "longitude": -118.24,
        "has_garage": False,
        "has_pool": False,
        "price": 420000.0,
    },
    {
        "living_area_sqft": 3500.0,
        "lot_size_sqft": 12000.0,
        "bedrooms": 5,
        "bathrooms": 3.5,
        "year_built": 2018,
        "latitude": 40.71,
        "longitude": -74.01,
        "has_garage": True,
        "has_pool": True,
        "price": 1250000.0,
    },
]
