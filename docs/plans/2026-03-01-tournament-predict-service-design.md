# Tournament Predict Service — Design

**Date:** 2026-03-01
**Status:** Approved

## Overview

A generic `TournamentPredictService` for batch-oriented, round-based competitions. Participants submit models that receive a batch of features, return predictions, and are scored against ground truth that arrives separately. Ships in the base `crunch-node` package — not tied to any specific competition.

## Context

The existing `RealtimePredictService` handles continuous, event-driven prediction loops (streaming market data, tick-per-update, horizon-based resolution). A different pattern is needed for tournament-style competitions where:

- Input arrives as a batch (not a stream)
- Models are stateless (no ticking)
- Ground truth arrives separately, after inference
- Rounds are explicit, triggered by API calls (not scheduled)

## Architecture

### TournamentPredictService

Extends `PredictService`. Reuses model runner integration, record building, output validation. Does **not** use `FeedReader`, `run()` loop, `_tick_models()`, or `ScheduledPrediction`.

Two methods drive the round lifecycle:

#### `run_inference(round_id, features_data) → list[PredictionRecord]`

1. Validate features as `InferenceInput`
2. Save `InputRecord` (features only, no ground truth)
3. Call all registered models via model runner with the features batch
4. Save `PredictionRecord` per model with `scope_key = round_id`
5. No `resolvable_at` set — predictions wait for explicit scoring

#### `score_round(round_id, ground_truth_data) → list[ScoreRecord]`

1. Validate ground truth as `GroundTruth`
2. Query predictions by `scope_key = round_id`
3. Call `scoring_function(prediction, ground_truth)` per model
4. Save `ScoreRecord`s
5. Mark predictions as `SCORED`
6. Score service's existing loop picks up `ScoreRecord`s → snapshots → leaderboard → checkpoints

### API Endpoints

Two endpoints, auto-discovered:

```
POST /tournament/rounds/{round_id}/inference
```
- Accepts features file (JSON batch of records)
- Calls `TournamentPredictService.run_inference()`
- Returns prediction summary (model count, statuses)

```
POST /tournament/rounds/{round_id}/score
```
- Accepts ground truth file (JSON)
- Calls `TournamentPredictService.score_round()`
- Returns scoring summary (model count, scores)

### Round Concept

A round is just a `scope_key` string (e.g. `"round-001"`). No new table, no round entity. Predictions are grouped and queried by `scope_key`. This is sufficient for now — a formal `TournamentRound` entity can be added later if lifecycle tracking is needed.

## What Stays Unchanged

- **Score service** — existing loop handles snapshots, leaderboard, checkpoints from `ScoreRecord`s. No modifications.
- **PredictionRecord / ScoreRecord** — existing entities, no schema changes.
- **Leaderboard aggregation** — rolling windows average the score field across rounds.

## What's New (base package)

| Component | Location | Description |
|-----------|----------|-------------|
| `TournamentPredictService` | `crunch_node/services/tournament_predict.py` | PredictService subclass, round-based |

## What's in the Pack (scaffold)

| Component | Location | Description |
|-----------|----------|-------------|
| Tournament API endpoints | `scaffold/node/api/tournament.py` | Two POST endpoints calling the service |
| CrunchConfig | `scaffold/node/config/crunch_config.py` | `predict_service_class = TournamentPredictService` |
| `input_type` | CrunchConfig | Features schema (what models receive) |
| `ground_truth_type` | CrunchConfig | Ground truth schema (what scoring uses) |
| `output_type` | CrunchConfig | What models return |
| `score_type` | CrunchConfig | What scoring produces |
| `scoring_function` | CrunchConfig | Competition-specific scoring logic |

## What's Explicitly Out of Scope

- No feeds, no polling, no scheduled triggers
- No `resolve_horizon_seconds` — ground truth arrives via explicit API call
- No `_tick_models()` — tournament models are stateless
- No `ScheduledPrediction` config — rounds are triggered manually
- No round entity / table — `scope_key` is sufficient
- No competition-specific logic in the base package (winner selection, duplicate detection, emission distribution are all in the scaffold's `scoring_function`)

## Key Differences from RealtimePredictService

| Aspect | Realtime | Tournament |
|--------|----------|------------|
| Trigger | pg NOTIFY / feed poll | Explicit API calls |
| Loop | Continuous `run()` loop | No loop — request-driven |
| Input | Latest candles from feed | Uploaded features file |
| Model call | Per-tick, incremental | Per-round, full batch |
| Ticking | Sends data to models continuously | No tick — models are stateless |
| Ground truth | Resolved via horizon or feed window | Uploaded separately per round |
| Scoring | Score service resolves actuals | Predict service scores directly |
| Round concept | Implicit (each tick) | Explicit (`scope_key` string) |

## Implementation Plan

### Step 1: TournamentPredictService (base package)

1. Create `crunch_node/services/tournament_predict.py`
2. Extend `PredictService`
3. Implement `run_inference()` and `score_round()`
4. Override `run()` as a no-op / wait-for-shutdown

### Step 2: Tournament pack (scaffold)

1. Create `scaffold/node/api/tournament.py` — two POST endpoints calling the service
2. CrunchConfig with `predict_service_class = TournamentPredictService`
3. Types, scoring function
4. Auto-discovered by existing API discovery

### Step 3: Wire into predict_worker

1. `_resolve_service_class()` already supports `config.predict_service_class`
2. No changes needed — scaffold sets `predict_service_class = TournamentPredictService`

### Step 4: Benchmark

1. Create a benchmark spec for a tournament competition
2. Use RESI-style property valuation as the test case
3. Generate synthetic in-sample (1K properties) and out-of-sample (200 properties) data matching RESI's 80-feature schema
4. Verify full pipeline: upload features → inference → upload ground truth → score → leaderboard
