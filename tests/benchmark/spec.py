"""Fixed benchmark spec — btc-direction-v1.

Contains the prompt given to the agent and the expected values
used by verify.py to check milestones.
"""

from __future__ import annotations

SPEC_VERSION = "btc-direction-v3"

AGENT_PROMPT = """\
Build a BTC price direction competition from this scaffold workspace.

Read .agents/guide.md to understand the architecture. Then implement ALL code
changes FIRST (steps 1-5 below), run `make test`, and ONLY THEN deploy.

CRITICAL TIME MANAGEMENT:
- Do NOT run `make deploy` or `make verify-e2e` until ALL code changes are done
- Do NOT verify the baseline scaffold — it works, just start implementing
- Do NOT run `make logs` — it follows logs forever and wastes your time budget
- Do NOT use `sleep` commands — `make verify-e2e` polls internally
- Implement everything, run `make test`, THEN deploy and verify

All base types (InferenceOutput, GroundTruth, ScoreResult, etc.) are imported
from `crunch_node.crunch_config` in node/config/crunch_config.py. Override them
in your CrunchConfig subclass. The defaults have a single `value: float` field.

## Step 1: Types (edit node/config/crunch_config.py)

InferenceOutput — what models return:
- direction: str = "hold"  — must be "up" or "down"
- confidence: float = 0.0  — between 0.0 and 1.0

ScoreResult — what scoring produces:
- value: float = 0.0
- success: bool = True
- failed_reason: str | None = None

GroundTruth — what the actual outcome looks like:
- profit: float = 0.0
- direction_up: bool = True
IMPORTANT: You MUST override the GroundTruth class with these fields (with defaults).
The score worker dry-runs scoring at startup using GroundTruth() defaults.
If the fields don't exist, scoring raises a KeyError and the worker crashes.
Set ground_truth_type = BtcGroundTruth (or whatever you name it) in CrunchConfig.

TrackerBase is imported as: `from crunch_node.cruncher import ModelBaseClass as TrackerBase`
(this import is already in cruncher.py — check it to see the pattern)

## Step 2: Examples (edit challenge/starter_challenge/examples/)

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

## Step 3: Scoring (edit challenge/starter_challenge/scoring.py)

score_prediction(prediction, ground_truth) -> dict:
- The scoring function receives typed Pydantic objects, not dicts.
  Use attribute access: prediction.direction, ground_truth.profit, etc.
- If prediction.direction matches ground truth direction:
    score = +prediction.confidence * abs(ground_truth.profit)
- If wrong:
    score = -prediction.confidence * abs(ground_truth.profit)
- Ground truth has attributes: profit (float), direction_up (bool)
- prediction.direction == "up" should be compared to ground_truth.direction_up
- Always return {"value": score, "success": True, "failed_reason": None}

## Step 4: Tests

- Update test_scoring.py: remove xfail markers after implementing scoring
- Update test_examples.py if needed for new output shape
- Run `make test` — fix until all tests pass

## Step 5: Deploy & Verify

ONLY after all code changes are done and `make test` passes:
- Run `make deploy`
- If port conflicts: run `make down`, then `docker rm -f $(docker ps -aq --filter name=crunch-node-) 2>/dev/null || true`, then retry
- Run `make verify-e2e` (it polls internally — run it immediately after deploy)
- If verify fails, check container logs with `docker compose -f docker-compose.yml --env-file .local.env logs --tail=50 <service>` (do NOT use `make logs`)
- Fix and retry

## Ground Truth (do NOT override)

The default resolve_ground_truth already computes profit and direction_up
from feed records. It returns:
{"symbol", "asof_ts", "entry_price", "resolved_price", "profit", "direction_up"}.
This matches what the scoring function needs — you do NOT need to override it.

## Schedule & Feed (do NOT change)

Keep the scaffold defaults — do NOT change .local.env feed settings:
- subject: BTCUSDT
- prediction_interval_seconds: 15
- resolve_horizon_seconds: 60
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

EXPECTED_GROUND_TRUTH_FIELDS = {
    "profit": "float",
    "direction_up": "bool",
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
        {"profit": 0.02, "direction_up": True},
        "positive",
    ),
    # Wrong: up prediction, price went down → negative
    (
        {"direction": "up", "confidence": 0.8},
        {"profit": -0.02, "direction_up": False},
        "negative",
    ),
    # Correct: down prediction, price went down → positive
    (
        {"direction": "down", "confidence": 0.6},
        {"profit": -0.03, "direction_up": False},
        "positive",
    ),
    # Wrong: down prediction, price went up → negative
    (
        {"direction": "down", "confidence": 0.6},
        {"profit": 0.03, "direction_up": True},
        "negative",
    ),
]
