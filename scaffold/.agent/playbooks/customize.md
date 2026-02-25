# Playbook: Customize Competition

Use this when changing the competition's types, scoring, feeds, emission, or model interface.

## Before you start

1. Read `.agent/context.md` — especially Contract-Based Design and Extension points
2. Read `.agent/policy.md` — emission and scoring changes may require approval
3. Read `node/.agent/context.md` for node-specific edit boundaries
4. Read `challenge/.agent/context.md` for challenge-specific guidance

## ⚠️ Starter placeholder values — do NOT carry over

The scaffold ships with placeholder values that make it run out of the box.
**These are NOT sensible defaults for any real competition.** Every value
below must be explicitly confirmed with the user before proceeding:

| Placeholder | Where | What to ask |
|---|---|---|
| `subject: "BTC"` | PredictionScope, tracker, examples, env | What asset(s) is this competition about? |
| `horizon_seconds: 60` | PredictionScope, scheduled_prediction_configs | What prediction horizon makes sense? |
| `step_seconds: 15` | PredictionScope | What time step between predictions? |
| `prediction_interval_seconds: 60` | scheduled_prediction_configs.json | How often should models predict? |
| `resolve_after_seconds: 60` | scheduled_prediction_configs.json | How long to wait for ground truth? (must exceed feed interval) |
| `FEED_SOURCE: pyth` | .local.env | What data source? |
| `FEED_GRANULARITY: 1s` | .local.env | What data granularity? |
| `InferenceOutput.value: float` | crunch_config.py, tracker, scoring | What should models return? |
| `scoring: return 0.0` | scoring.py | How should predictions be scored? |
| `metrics: [ic, ic_sharpe, ...]` | CrunchConfig.metrics | Which metrics matter for this competition? |
| `ranking_key: score_recent` | Aggregation | What metric determines leaderboard rank? |
| `tiers: 1st=35%, 2-5=10%, ...` | build_emission | How should rewards be distributed? |

**Rule:** Do not copy these values into a new competition. Ask the user for
each one. If the user says "use the defaults," confirm explicitly which
defaults they mean — the starter values may not match their domain.

## Design checklist

Before implementing, confirm these are defined:

1. **Model interface** — tracker class that participants implement
2. **Scoring function** — how predictions are scored against actuals
3. **Feed configuration** — source, subjects, kind, granularity
4. **Prediction schedule** — `prediction_interval_seconds` and `resolve_after_seconds`
5. **Ground truth resolution** — how actuals are derived from feed data
6. **Emission config** — crunch pubkey, provider wallets, tier distribution

If any are missing, ask the user before proceeding.

### Critical: `resolve_after_seconds` must exceed feed granularity

`resolve_after_seconds` defines how long the score-worker waits before looking up ground truth from the feed. If this value is shorter than the feed's data interval (`FEED_GRANULARITY` / `FEED_POLL_SECONDS`), **no ground truth data will exist yet** and all predictions will fail to score.

**Rule:** `resolve_after_seconds` must be **strictly greater** than the feed's effective data interval. Ask the user what value makes sense for their use case — do not guess.

Examples:
- Feed granularity `1s`, poll every `5s` → `resolve_after_seconds` must be > 5
- Feed granularity `1m` → `resolve_after_seconds` must be > 60
- Feed granularity `5m` → `resolve_after_seconds` must be > 300

## Workflow

### 0. Run scaffold integration tests FIRST

Before writing any code, run the tests that verify the scaffold wiring:

```bash
# From workspace root (scaffold/):
make test

# Or from the repo root to include CrunchConfig integration tests:
cd .. && PYTHONPATH=scaffold/challenge:scaffold/node make test
```

**Two test suites exist:**

#### Challenge tests (`challenge/tests/`)

| File | What it checks |
|---|---|
| `test_tracker.py` | TrackerBase per-subject data isolation, fallback, edge cases |
| `test_scoring.py` | Scoring function contract (shape/types) + **stub detection** |
| `test_examples.py` | Example trackers: contract compliance, boundary cases, multi-subject isolation |

The scoring tests use `xfail(strict=True)` markers for behavioral expectations
(e.g. "correct prediction scores positive"). **These are designed to fail against
the 0.0 stub.** When you implement real scoring, remove the `xfail` markers —
if you forget, the tests will break (strict xfail that unexpectedly passes = failure).

#### Scaffold integration tests (`tests/test_scaffold_integration.py`)

| Class | What it catches |
|---|---|
| `TestConfigFileValid` | Malformed `scheduled_prediction_configs.json`, typos in schedule keys |
| `TestScopeTemplateAlignment` | `scope_template` keys that don't match `PredictionScope` fields; `CallMethodConfig.args` that can't resolve from the merged scope |
| `TestGroundTruthResolution` | `resolve_ground_truth` returning None for valid data, missing keys, zero returns |
| `TestScoringPipelineRoundtrip` | Scoring function KeyErrors on `InferenceOutput` defaults; output that doesn't validate as `ScoreResult` |
| `TestAggregationRoundtrip` | Empty aggregation; ranking_key not in any known source |
| `TestTrackerOutputMatchesInferenceOutput` | Tracker `predict()` output doesn't validate as `InferenceOutput`; full roundtrip tracker→scoring KeyError |

**Use these as TDD targets:** read which tests are failing or xfailing, implement
the customization to make them pass, then verify all green before deploying.

### 1. Scoring function (do this FIRST)

The scoring function is the most important file in the competition. It defines
what "good" means. **Do not leave it as a stub that returns 0.0** — a pipeline
that scores everything as zero produces meaningless leaderboards silently.

**Steps:**
1. Ask the user: "How should predictions be scored against actuals?" Do not guess.
2. Implement real scoring logic in `challenge/starter_challenge/scoring.py`
3. Wire it in `node/config/callables.env`: `SCORING_FUNCTION=starter_challenge.scoring:score_prediction`
4. Write a unit test that feeds a known prediction + ground truth and asserts a **non-zero** score
5. Ensure `node/runtime_definitions/crunch_config.py` ScoreResult fields match what the function returns

**The scoring function receives:**
- `prediction` — dict with the model's output (matches `InferenceOutput` shape)
- `ground_truth` — dict from `resolve_ground_truth` (see step 3 below)

**It must return:** a dict matching `ScoreResult` — at minimum `{"value": float, "success": bool, "failed_reason": str | None}`

### 2. Ground truth resolution

`resolve_ground_truth` determines what "actually happened" from feed data.
This is the second most important function — if it returns None or zero,
all scores will be zero regardless of model quality.

**Sanity check:** after implementing, verify that the resolver produces
non-zero returns with your configured feed granularity. A 60s horizon with
1m candles can produce 0.0 returns if only one candle falls in the window.

- Default: compares first/last record's close price → returns `entry_price`, `resolved_price`, `return`, `direction_up`
- Override in `CrunchConfig.resolve_ground_truth` for custom logic (VWAP, cross-venue, labels, etc.)

### 3. Types and shapes

Edit `node/runtime_definitions/crunch_config.py`:
- `RawInput` — what the feed produces
- `InferenceInput` — what models receive (can differ from RawInput via transform)
- `InferenceOutput` — what models return
- `ScoreResult` — what scoring produces (must match scoring function output)
- `PredictionScope` — prediction context (subject, horizon, step)

**Critical: `InferenceOutput` keys must be consistent across all three places:**
1. `InferenceOutput` class fields (e.g. `value: float`)
2. The scoring function reads from `prediction` dict using the same keys (e.g. `prediction["value"]`)
3. The tracker's `predict()` method in `challenge/` returns a dict with those same keys

A mismatch (e.g. `InferenceOutput.value` vs model returning `{"score": 0.5}`)
silently produces wrong results — the scoring function reads a missing key
and falls back to 0.0 without error. **Verify all three match before deploying.**

### 4. Multi-metric scoring

- Add/remove metric names in `CrunchConfig.metrics`
- Custom metrics: register via `get_default_registry().register("name", fn)`

### 6. Feeds

Edit `node/.local.env`:
- `FEED_SOURCE` (pyth, binance, etc.)
- `FEED_SUBJECTS` (BTC, ETH, etc. — comma-separated for multi-asset)
- `FEED_KIND` (tick, candle)
- `FEED_GRANULARITY` (1s, 1m, etc.)

#### Multi-asset competitions

Multi-asset is **natively supported** — do NOT query the DB directly for
ground truth or build custom feed ingestion per subject.

**How it works end-to-end:**

1. Set `FEED_SUBJECTS=BTCUSDT,ETHUSDT,SOLUSDT` in `node/.local.env`
2. The feed-data-worker automatically ingests all subjects into `feed_records`
3. `scheduled_prediction_configs.json` defines scopes per subject (or a single
   scope with subject as a template variable):
   ```json
   [
     {"scope_key": "BTCUSDT-60", "scope_template": {"subject": "BTCUSDT", "horizon_seconds": 60, "step_seconds": 60}, ...},
     {"scope_key": "ETHUSDT-60", "scope_template": {"subject": "ETHUSDT", "horizon_seconds": 60, "step_seconds": 60}, ...}
   ]
   ```
4. The predict-worker creates separate predictions per scope, each tagged with `subject`
5. The score-worker resolves ground truth **per prediction** using `inp.scope["subject"]`
   to filter feed records — it calls `feed_reader.fetch_window(subject=inp.scope["subject"], ...)`
6. `resolve_ground_truth` receives only the feed records for that specific subject

**Common mistakes to avoid:**
- Do NOT write a custom `resolve_ground_truth` that queries the DB for other subjects — the framework already filters by subject
- Do NOT use a single scope_key for all subjects — each subject needs its own scope entry
- Do NOT hardcode a subject in `resolve_ground_truth` — it receives pre-filtered records for the correct subject
- Ensure `FEED_SUBJECTS` in `.local.env` matches the subjects in `scheduled_prediction_configs.json`

### 7. Emission (requires approval)

Edit `CrunchConfig`:
- `crunch_pubkey` — on-chain crunch account
- `compute_provider`, `data_provider` — wallet pubkeys
- `build_emission` — reward distribution logic

### 8. Challenge package

Edit `challenge/starter_challenge/`:
- `tracker.py` — model interface participants implement
- `scoring.py` — local self-eval scoring (should match runtime scoring)
- `examples/` — quickstarter implementations

### 9. Validate

```bash
# Unit + integration tests (no Docker required)
make test
cd .. && PYTHONPATH=scaffold/challenge:scaffold/node make test

# Full E2E (requires Docker)
cd node
make deploy
make verify-e2e
```

**All three must pass:**
1. `make test` in `scaffold/` — challenge tests green, scoring xfails removed
2. `make test` at repo root — scaffold integration tests green
3. `make verify-e2e` — full pipeline produces non-zero scores

If scoring xfail tests still pass as xfail, you haven't implemented real scoring yet.
If scaffold integration tests fail, the pipeline will break at runtime.

### 10. Complete

Produce:
- Summary of what was customized
- Design checklist status (all 5 items confirmed)
- Verification result
- Any assumptions about scoring or emission behavior
