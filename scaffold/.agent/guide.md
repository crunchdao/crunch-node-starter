# Implementation Guide

How to build each component. Follow this order — later steps depend on earlier ones.

## Starter Placeholders

The scaffold ships with working-but-meaningless values. **Confirm every one with the user:**

| Placeholder | Where |
|---|---|
| `subject: "BTCUSDT"` | CrunchConfig, tracker, examples, `.local.env` |
| `horizon_seconds: 60` | `scheduled_predictions` |
| `prediction_interval_seconds: 15` | `scheduled_predictions` |
| `FEED_SOURCE: binance` | `.local.env` |
| `FEED_GRANULARITY: 1s` | `.local.env` |
| `InferenceOutput.value: float` | `crunch_config.py`, tracker, scoring |
| `scoring: return 0.0` | `scoring.py` (stub) |
| `ranking_key: score_recent` | `Aggregation` |

If the user says "use defaults," ask which specific values they mean.

## Use Tests as Implementation Targets

```bash
make test
```

Three test files track your progress:
- `test_tracker.py` — TrackerBase behavior (passes out of the box)
- `test_examples.py` — example models match `InferenceOutput` contract (breaks when you change types without updating examples)
- `test_scoring.py` — has `xfail(strict=True)` markers that detect the 0.0 stub. **Remove xfail markers after implementing real scoring** — strict xfail that unexpectedly passes = test failure.

## 1. Types and Tracker

Define the contract first — what models receive and what they return.

**Types** — edit `node/config/crunch_config.py`:

All types are Pydantic models imported from `crunch_node.crunch_config`.
The scaffold's `CrunchConfig` subclass overrides them. The base defaults all
have a single `value: float` field. Override by defining your own Pydantic
model and assigning it in your CrunchConfig class:

```python
from crunch_node.crunch_config import CrunchConfig as _Base, InferenceOutput

class MyOutput(InferenceOutput):
    # define whatever fields models should return for this competition
    ...

class CrunchConfig(_Base):
    output_type = MyOutput
```

**IMPORTANT — all type fields MUST have defaults.** The score worker
dry-runs the scoring function at startup using `GroundTruth()` and
`InferenceOutput()` (no args). If any field lacks a default, this raises
a `ValidationError` and the worker crashes on boot.

**GroundTruth should match what `resolve_ground_truth` returns.** The
default resolver returns raw candle data from the first and last feed
records in the window:
`{symbol, asof_ts, entry_candles_1m, resolved_candles_1m}`. If your
scoring needs derived fields (profit, direction), either override
`resolve_ground_truth` to compute them, or compute them inside the
scoring function from the candle data.

The types to override:
- `output_type` — what models return (this is the core design decision)
- `ground_truth_type` — actual outcome shape (needs defaults!). Must match what `resolve_ground_truth` returns.
- `score_type` — what scoring produces (define after scoring, step 3)

Optional:
- `input_type` — override only for non-feed modes (tournament API). For feed-based modes, the `feed_normalizer` setting determines the input shape.
- `feed_normalizer` — `"candle"` (default, OHLCV) or `"tick"` (raw price ticks)

**Tracker** — edit `challenge/starter_challenge/tracker.py`:
- `feed_update(data)` — receives market data, maintains state per-subject via `data["symbol"]`
- `predict(subject, resolve_horizon_seconds, step_seconds)` — returns dict matching `InferenceOutput`

The tracker defines the participant interface. What `predict()` returns IS the competition.

### TrackerBase API

Models extend `TrackerBase`. Key methods:

| Method | Purpose |
|---|---|
| `feed_update(data)` | Called with each feed update. Stores data by `data["symbol"]`. |
| `_get_data(subject)` | Returns the latest feed data dict for a subject. |
| `predict(subject, resolve_horizon_seconds, step_seconds)` | Must return a dict matching `InferenceOutput`. |

The `data` dict passed to `feed_update()` has shape:
```python
{"symbol": "...", "asof_ts": int, "candles_1m": [{"ts": int, "open": float, "high": float, "low": float, "close": float, "volume": float}, ...]}
```

In `predict()`, call `self._get_data(subject)` to access the latest data, then extract what you need from `candles_1m`.

## 2. Examples

Edit `challenge/starter_challenge/examples/`. Build ~3-5 simple models:
- **Simple logic** — mean-reversion, trend-follow, always-long, etc.
- **Diverse** — different strategies so they produce different scores/rankings
- **Predictable** — you should know roughly what to expect from each
- **Contract-compliant** — `predict()` returns a dict matching `InferenceOutput`

These are NOT just quickstarters for participants — they are **your E2E test models**. After `make deploy`, they run through the full pipeline. If they break, you can't verify the system works.

Run `make test` — `test_examples.py` and `test_tracker.py` should pass.

## 3. Feeds

Edit `node/.local.env`: `FEED_SOURCE`, `FEED_SUBJECTS`, `FEED_KIND`, `FEED_GRANULARITY`.

**`resolve_horizon_seconds` must exceed feed granularity** — otherwise the score worker's fetch_window returns zero records and predictions silently fail to score.

## 4. Ground Truth Resolution

How "what actually happened" is derived from feed data. If this returns None or zero, all scores are zero regardless of model quality.

- Default: returns raw candle data from first/last feed records → `{symbol, asof_ts, entry_candles_1m, resolved_candles_1m}`
- Override: set `CrunchConfig.resolve_ground_truth` to compute derived fields (profit, direction, VWAP, etc.)
- Signature: `resolve_ground_truth(feed_records, prediction)` — receives all feed records in the window plus the prediction being scored. Use `prediction.scope` to filter records in multi-asset competitions.
- Returns a dict or Pydantic model. If a Pydantic model, the score service calls `.model_dump()` automatically.

**Verify** non-zero returns with your feed granularity. A 60s horizon with 1m candles may produce 0.0 returns if only one candle falls in the window.

## 5. Scoring Function

Now that you know what models produce (step 1-2) and what ground truth looks like (step 4), define evaluation.

A stub returning 0.0 produces meaningless leaderboards silently — everything "works" but nothing is real.

1. Implement in `challenge/starter_challenge/scoring.py` (already wired as `SCORING_FUNCTION` in `node/.local.env`)
2. Remove `xfail` markers from `challenge/tests/test_scoring.py`
3. Run `make test` — all green

**Receives:** `prediction` (typed Pydantic `output_type` instance), `ground_truth` (typed Pydantic `ground_truth_type` instance).
Access fields via attribute access (`prediction.direction`, `ground_truth.profit`), not dict access.
**Returns:** dict or Pydantic model matching `ScoreResult` — at minimum `{"value": float, "success": bool, "failed_reason": str | None}`

Now update `ScoreResult` in `node/config/crunch_config.py` if your scoring returns additional fields.

**Key consistency check:** the score worker dry-runs the scoring function at startup against default `InferenceOutput()` and `GroundTruth()` values. An `AttributeError` or `KeyError` raises a hard `RuntimeError` — check `make logs` if the score worker fails to start.
