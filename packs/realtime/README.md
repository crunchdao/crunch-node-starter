# Prediction Competition

The simplest real-time competition. Models predict the next-minute
return of BTCUSDT and get scored every 60 seconds — fast enough to
see results within minutes, not days. A good starting point for
understanding how the coordinator node works end-to-end.

## Quick start

```bash
crunch-node init mycomp --pack realtime
cd mycomp
make deploy
```

Open `http://localhost:3000` — the leaderboard populates within ~3 minutes.

## What models do

Your model receives live 1-minute candle data from Binance and must
predict the price return over the next 60 seconds.

### 1. Receive data — `feed_update(data)`

Called every ~15 seconds with the latest candles:

```python
{
    "symbol": "BTCUSDT",
    "asof_ts": 1700000000000,
    "candles_1m": [
        {"ts": ..., "open": 69000.0, "high": 69050.0,
         "low": 68980.0, "close": 69020.0, "volume": 12.5},
        ...
    ]
}
```

Use this to maintain internal state — moving averages, momentum
indicators, order flow signals, etc.

### 2. Return a prediction — `predict(subject, resolve_horizon_seconds, step_seconds)`

Return a dict with a single `value` field — your predicted return:

```python
{"value":  0.0003}   # expect +0.03% (price going up)
{"value": -0.0005}   # expect -0.05% (price going down)
{"value":  0.0}      # no view / skip
```

The magnitude is your conviction. Larger values = bigger bets.

## Scoring

```
score = prediction × actual_return
```

This is a **linear scoring rule** — the same used in professional quant
tournaments (Numerai, Two Sigma). It is:

- **Proper** — the optimal strategy is to output your honest expected
  return. No gaming possible.
- **Symmetric** — correctly predicting down is worth the same as
  correctly predicting up.
- **Magnitude-aware** — larger predictions score more when correct,
  but cost more when wrong.

| You predict | Price moves | Score |
|-------------|-------------|-------|
| +0.001 | +0.0005 | +0.0000005 ✅ |
| +0.001 | -0.0005 | -0.0000005 ❌ |
| -0.002 | -0.0003 | +0.0000006 ✅ |
| 0.0 | anything | 0.0 (neutral) |

## Ground truth

After each 60-second resolution horizon, the engine compares the entry
price (candle close at prediction time) to the resolved price (candle
close 60s later) and computes:

```
actual_return = (resolved_price - entry_price) / entry_price
```

## Timing

| Parameter | Value |
|-----------|-------|
| Feed | Binance BTCUSDT 1-minute candles |
| Prediction interval | Every 15 seconds |
| Resolution horizon | 60 seconds |
| Score cycle | Every 60 seconds |

After deploying, expect **~2–3 minutes** before the first scores
appear on the leaderboard:

1. **~60s** — model containers build and connect
2. **~15s** — first prediction saved (next feed cycle)
3. **~60s** — resolution horizon elapses
4. **~60s** — next score worker cycle picks up resolvable predictions

The leaderboard at `http://localhost:3000` populates as soon as
step 4 completes — the same cycle that scores predictions also
writes snapshots and updates the leaderboard rankings.

## Getting started

Subclass `TrackerBase` and implement `_predict()`:

```python
from starter_challenge.tracker import TrackerBase

class MyTracker(TrackerBase):
    def _predict(self, subject, resolve_horizon_seconds, step_seconds):
        prices = self._closes(self._get_data(subject))
        if len(prices) < 5:
            return {"value": 0.0}

        # Your signal here
        avg = sum(prices[-5:]) / 5
        predicted_return = (avg - prices[-1]) / prices[-1]
        return {"value": predicted_return}

    @staticmethod
    def _closes(data):
        if not data:
            return []
        return [float(c["close"]) for c in data.get("candles_1m", [])]
```

### Example models included

| Model | Strategy | File |
|-------|----------|------|
| Momentum | Projects recent trend forward | `examples/momentum_tracker.py` |
| Mean Reversion | Bets on return to rolling mean | `examples/mean_reversion_tracker.py` |
| Contrarian | Fades the last candle's move | `examples/contrarian_tracker.py` |

## Project structure

```
challenge/          # Participant-facing package
  {name}/
    tracker.py      # TrackerBase — subclass this
    scoring.py      # score = prediction × actual_return
    examples/       # 3 example models
  tests/            # Challenge tests
node/               # Competition infrastructure
  config/           # CrunchConfig
  docker-compose.yml
  Makefile
webapp/             # Report UI (cloned separately)
```

## Run locally

```bash
cd node
make deploy         # Build and start all containers
make verify-e2e     # Verify the full pipeline
```

Then open `http://localhost:3000` for the leaderboard.
