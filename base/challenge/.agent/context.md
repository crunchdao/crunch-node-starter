# Challenge Context — starter-challenge

## What this is

Participant-facing Python package. Contains the model interface, scoring for local self-eval, backtest harness, and quickstarter examples.

## Primary implementation files

| File | Purpose |
|---|---|
| `starter_challenge/tracker.py` | Model interface — participants implement this |
| `starter_challenge/scoring.py` | Scoring function for local self-eval |
| `starter_challenge/backtest.py` | Backtest harness for local model evaluation |
| `starter_challenge/config.py` | Baked-in coordinator URL and feed defaults |
| `starter_challenge/examples/` | Quickstarter implementations |

## Model interface

```python
class TrackerBase:
    def tick(self, data: dict) -> None:
        """Receive market data. Override to maintain state."""

    def predict(self, subject: str, resolve_horizon_seconds: int, step_seconds: int) -> dict:
        """Return a prediction dict matching InferenceOutput."""
        raise NotImplementedError
```

- `tick()` receives per-symbol market data, stores per subject
- `predict()` is what participants implement — returns dict matching `output_type`
- `resolve_horizon_seconds=0` is valid (immediate resolution for live trading)

## Backtest harness

- **BacktestClient** — fetches parquet data from coordinator, caches locally
- **BacktestRunner** — replays historical data through models (tick → predict → score)
- **BacktestResult** — notebook-friendly output (DataFrames, rolling window metrics)

## Cross-references

- Runtime config: `../node/config/crunch_config.py` — CrunchConfig defining types, scoring, emission

## Development guidance

- Keep participant-facing challenge logic in this package
- Keep runtime contracts and deployment config in `../node/`
- The scoring function in `scoring.py` is for local self-eval. Runtime scoring is in `crunch_config.py`
- When publishing, set `COORDINATOR_URL` in `config.py`

## Tests

```bash
make test   # from workspace root
```

| File | Purpose |
|---|---|
| `tests/test_tracker.py` | TrackerBase per-subject data isolation, fallback |
| `tests/test_scoring.py` | Scoring contract + behavioral stub detection |
| `tests/test_examples.py` | All example trackers: contract compliance |
