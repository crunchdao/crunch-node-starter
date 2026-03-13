"""Trading benchmark spec — trading-order-v1.

Contains the prompt given to the agent and expected values
used by verify.py to check milestones.
"""

from __future__ import annotations

SPEC_VERSION = "trading-order-v1"

AGENT_PROMPT = """\
Validate and deploy a trading competition from this workspace.

The trading pack has already been overlaid onto the scaffold. Your job is to
verify everything works, fix any issues, deploy, and verify end-to-end.

CRITICAL TIME MANAGEMENT:
- Do NOT run `make deploy` or `make verify-e2e` until you've verified code is correct
- Do NOT run `make logs` — it follows logs forever and wastes your time budget
- Do NOT use `sleep` commands — `make verify-e2e` polls internally
- Fix code first, run `make test`, THEN deploy and verify

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

1. Read the code and fix any issues in types, examples, tests
2. Run `make test` — fix any failures
3. Run `make deploy` — if port conflicts: run `make down`, then `docker rm -f $(docker ps -aq --filter name=crunch-node-) 2>/dev/null || true`, then retry
4. Run `make verify-e2e` immediately after deploy
5. If verify fails, check container logs with `docker compose -f docker-compose.yml --env-file .local.env logs --tail=50 <service>` (do NOT use `make logs`)
6. Fix and retry
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
