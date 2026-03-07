"""Fixed benchmark spec — btc-direction-v1.

Contains the prompt given to the agent and the expected values
used by verify.py to check milestones.
"""

from __future__ import annotations

SPEC_VERSION = "btc-direction-v3"

AGENT_PROMPT = """\
Build a BTC price direction competition from this scaffold workspace.

Read the .agent/ docs to understand the workflow. Follow the implementation
guide. Run make test, make deploy, and make verify-e2e yourself. Read logs
and fix any issues until everything passes.

IMPORTANT: All base types (InferenceOutput, GroundTruth, ScoreResult, etc.)
are imported from `crunch_node.crunch_config` in node/config/crunch_config.py.
You don't need to find the library source — just override the types in your
CrunchConfig subclass. The defaults have a single `value: float` field.

Here is the exact specification:

## Types (edit node/config/crunch_config.py)

InferenceOutput — what models return:
- direction: str  — must be "up" or "down"
- confidence: float  — between 0.0 and 1.0

ScoreResult — what scoring produces:
- value: float
- success: bool
- failed_reason: str | None

GroundTruth — what the actual outcome looks like:
- profit: float = 0.0
- direction_up: bool = True
IMPORTANT: You MUST override the GroundTruth class with these fields (with defaults).
The score worker dry-runs scoring at startup using GroundTruth() defaults.
If the fields don't exist, scoring raises a KeyError and the worker crashes.
Set ground_truth_type = BtcGroundTruth (or whatever you name it) in CrunchConfig.

## Scoring (edit challenge/starter_challenge/scoring.py)

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

## Ground Truth

The default resolve_ground_truth returns raw candle data:
{"symbol", "asof_ts", "entry_candles_1m", "resolved_candles_1m"}.
You MUST override resolve_ground_truth in CrunchConfig to compute
the fields your scoring function needs (profit, direction_up).

Example resolve_ground_truth:
```python
def resolve_ground_truth(feed_records, prediction=None):
    if len(feed_records) < 2:
        return None
    entry = feed_records[0]
    resolved = feed_records[-1]
    entry_candles = entry.values.get("candles_1m", [])
    resolved_candles = resolved.values.get("candles_1m", [])
    if not entry_candles or not resolved_candles:
        return None
    entry_price = entry_candles[-1].get("close", 0.0)
    resolved_price = resolved_candles[-1].get("close", 0.0)
    if entry_price == 0:
        return None
    profit = (resolved_price - entry_price) / abs(entry_price)
    return {
        "profit": profit,
        "direction_up": resolved_price > entry_price,
    }
```

Set it in CrunchConfig:
```python
class CrunchConfig(BaseCrunchConfig):
    resolve_ground_truth = resolve_ground_truth
```

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

## Schedule & Feed (edit node/config/crunch_config.py scheduled_predictions)

IMPORTANT: Change these from the scaffold defaults:
- subject: BTCUSDT (keep default)
- prediction_interval_seconds: 15  (keep default)
- resolve_horizon_seconds: 60  (keep default)
- Feed: binance, kline, 1s granularity (keep defaults from .local.env)

## Tests

- Update test_scoring.py: remove xfail markers after implementing scoring
- Update test_examples.py if needed for new output shape
- make test must pass

## Deploy & Verify

- Run make deploy
- Run make verify-e2e (it has its own polling — do NOT sleep or wait before running it)
- Read logs with make logs if anything fails
- Fix and retry until make verify-e2e passes

IMPORTANT: Never use `sleep` commands. `make verify-e2e` already polls and
waits for the pipeline to be ready. Sleeping wastes your time budget.
If deploy fails due to port conflicts, run `make down` and retry on different ports.
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
