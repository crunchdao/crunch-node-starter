"""Trading benchmark spec — trading-order-v1.

Contains the prompt given to the agent and expected values
used by verify.py to check milestones.
"""

from __future__ import annotations

SPEC_VERSION = "trading-order-v1"

AGENT_PROMPT = """\
Validate and deploy a trading competition from this workspace.

The trading pack has already been overlaid onto the scaffold. Your job is to
verify everything works, deploy, and fix any issues.

## What's already configured

- node/config/crunch_config.py — Trading CrunchConfig with order-based signal mode
- challenge/starter_challenge/tracker.py — TrackerBase for trading models
- challenge/starter_challenge/examples/ — 3 example trackers (momentum, mean_reversion, breakout)
- .local.env — Feed config for Binance BTCUSDT/ETHUSDT candles

## Types

InferenceOutput — what models return:
- action: str  — "buy" or "sell"
- amount: float  — position size (>= 0)

No scoring function — PnL is computed by the TradingEngine, not a scoring function.

## Steps

1. Run make test — fix any failures
2. Run make deploy — fix any failures (port conflicts: run make down first)
3. Run make verify-e2e — fix any failures, read logs with make logs
4. Retry until make verify-e2e passes

IMPORTANT: Never use `sleep` commands. `make verify-e2e` already polls.
If deploy fails due to port conflicts, run `make down` and retry.
"""

EXPECTED_OUTPUT_FIELDS = {
    "action": "str",
    "amount": "float",
}

EXPECTED_EXAMPLES = [
    "momentum_tracker.py",
    "mean_reversion_tracker.py",
    "breakout_tracker.py",
]

EXPECTED_EXAMPLE_CLASSES = [
    "MomentumTracker",
    "MeanReversionTracker",
    "BreakoutTracker",
]
