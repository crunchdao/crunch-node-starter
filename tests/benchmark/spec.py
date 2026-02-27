"""Fixed benchmark spec — btc-direction-v1.

Contains the prompt given to the agent and the expected values
used by verify.py to check milestones.
"""

from __future__ import annotations

SPEC_VERSION = "btc-direction-v1"

AGENT_PROMPT = """\
Build a BTC price direction competition from this scaffold workspace.

Read the .agent/ docs to understand the workflow. Follow the implementation
guide. Run make test, make deploy, and make verify-e2e yourself. Read logs
and fix any issues until everything passes.

Here is the exact specification:

## Types (edit node/config/crunch_config.py)

InferenceOutput — what models return:
- direction: str  — must be "up" or "down"
- confidence: float  — between 0.0 and 1.0

ScoreResult — what scoring produces:
- value: float
- success: bool
- failed_reason: str | None

## Scoring (edit challenge/starter_challenge/scoring.py)

score_prediction(prediction, ground_truth) -> dict:
- If prediction["direction"] matches ground truth direction:
    score = +prediction["confidence"] * abs(ground_truth["return"])
- If wrong:
    score = -prediction["confidence"] * abs(ground_truth["return"])
- Ground truth has keys: "return" (float), "direction_up" (bool)
- prediction["direction"] == "up" should be compared to ground_truth["direction_up"]
- Always return {"value": score, "success": True, "failed_reason": None}

## Ground Truth

Use the default resolve_ground_truth (close price comparison).
Do NOT implement a custom one.

## Examples (edit challenge/starter_challenge/examples/)

Create exactly 3 example trackers:

1. always_up_tracker.py — AlwaysUpTracker
   - Always returns {"direction": "up", "confidence": 1.0}

2. momentum_tracker.py — MomentumTracker
   - Look at last 3 close prices from candles_1m
   - If trending up (last > first): direction = "up", else "down"
   - confidence = min(abs(last - first) / max(abs(first), 1e-9), 1.0)

3. mean_reversion_tracker.py — MeanReversionTracker
   - Opposite of momentum: if trending up predict "down", vice versa
   - Same confidence formula as momentum

All trackers extend TrackerBase. predict() returns a dict, not a Pydantic model.
Use _get_data(subject) to access latest tick data, extract closes from candles_1m.

## Schedule & Feed

Keep ALL defaults:
- subject: BTCUSDT
- prediction_interval_seconds: 15
- resolve_horizon_seconds: 60
- Feed: pyth, 1s granularity

## Tests

- Update test_scoring.py: remove xfail markers after implementing scoring
- Update test_examples.py if needed for new output shape
- make test must pass

## Deploy & Verify

- Run make deploy
- Run make verify-e2e
- Read logs with make logs if anything fails
- Fix and retry until make verify-e2e passes
"""

# --- Expected values for milestone verification ---

EXPECTED_OUTPUT_FIELDS = {
    "direction": "str",
    "confidence": "float",
}

EXPECTED_SCORE_FIELDS = {
    "value": "float",
    "success": "bool",
    "failed_reason": ("str", "None"),
}

EXPECTED_EXAMPLES = [
    "always_up_tracker.py",
    "momentum_tracker.py",
    "mean_reversion_tracker.py",
]

EXPECTED_EXAMPLE_CLASSES = [
    "AlwaysUpTracker",
    "MomentumTracker",
    "MeanReversionTracker",
]

# Scoring test cases: (prediction, ground_truth, expected_sign)
SCORING_TEST_CASES = [
    # Correct: up prediction, price went up → positive
    (
        {"direction": "up", "confidence": 0.8},
        {"return": 0.02, "direction_up": True},
        "positive",
    ),
    # Wrong: up prediction, price went down → negative
    (
        {"direction": "up", "confidence": 0.8},
        {"return": -0.02, "direction_up": False},
        "negative",
    ),
    # Correct: down prediction, price went down → positive
    (
        {"direction": "down", "confidence": 0.6},
        {"return": -0.03, "direction_up": False},
        "positive",
    ),
    # Wrong: down prediction, price went up → negative
    (
        {"direction": "down", "confidence": 0.6},
        {"return": 0.03, "direction_up": True},
        "negative",
    ),
]
