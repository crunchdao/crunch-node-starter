# Scaffold Benchmark — Design

Deterministic benchmark that measures how well an AI agent builds a crunch node from the scaffold given a fixed spec.

## Purpose

This repo's product is the scaffold + agent docs. The benchmark answers: "if I hand an agent a spec, does it produce a working coordinator?" and tracks that answer over time.

## The Spec

Fixed prompt, version `btc-direction-v1`:

> Build a BTC price direction competition:
>
> **Types:**
> - `InferenceOutput`: `direction: str` ("up"/"down"), `confidence: float` (0.0–1.0)
> - `ScoreResult`: `value: float`, `success: bool`, `failed_reason: str | None`
>
> **Scoring:**
> - Correct direction: `+confidence * abs(return)`
> - Wrong direction: `-confidence * abs(return)`
>
> **Ground truth:** Default (close price comparison → `return`, `direction_up`).
>
> **Examples (3 models):**
> - `always_up_tracker.py` — always `{"direction": "up", "confidence": 1.0}`
> - `momentum_tracker.py` — last 3 closes trending → direction, clamped abs momentum → confidence
> - `mean_reversion_tracker.py` — opposite of momentum, same confidence
>
> **Schedule/feed:** Keep defaults (BTCUSDT, 15s interval, 60s horizon, pyth 1s).

The agent receives a fresh `scaffold/` copy. It reads `.agent/` docs, edits code, runs `make test`, `make deploy`, `make verify-e2e`, reads logs, and fixes problems on its own.

## Harness Flow

1. **Setup** — Copy `scaffold/` to a temp directory. Write spec prompt.
2. **Invoke** — Run `$AGENT_CMD` with the spec prompt. Harness is passive.
3. **Verify** — After agent finishes or times out, independently check milestones.
4. **Record** — Write result JSON. Compare to previous run.

Timeout: 15 minutes (configurable).

## Milestones

| ID | Name | Check |
|----|------|-------|
| M1 | `types_correct` | Parse `crunch_config.py`, verify `InferenceOutput` has `direction: str` + `confidence: float` |
| M2 | `scoring_implemented` | Import `score_prediction`, call with known inputs, verify non-zero and correct sign |
| M3 | `examples_exist` | 3 tracker files exist, each `predict()` returns dict with `direction` + `confidence` |
| M4 | `tests_pass` | `make test` exit code 0 |
| M5 | `deploy_succeeded` | Docker containers running |
| M6 | `e2e_verified` | `make verify-e2e` exit code 0 |

## Result Schema

File: `tests/benchmark/results/YYYY-MM-DD-HH-MM-SS.json`

```json
{
  "timestamp": "2026-02-27T12:58:00Z",
  "agent_cmd": "pi",
  "spec_version": "btc-direction-v1",
  "duration_seconds": 342,
  "agent_exit_code": 0,
  "timed_out": false,
  "milestones": {
    "types_correct": {"passed": true, "details": "..."},
    "scoring_implemented": {"passed": true, "details": "..."},
    "examples_exist": {"passed": true, "details": "..."},
    "tests_pass": {"passed": true, "details": "..."},
    "deploy_succeeded": {"passed": true, "details": "..."},
    "e2e_verified": {"passed": false, "details": "..."}
  },
  "milestone_count": "5/6",
  "agent_log_file": "tests/benchmark/results/2026-02-27-12-58-00.log"
}
```

## Comparison

Loads previous result JSON, prints:

```
Previous: 2026-02-26  4/6 milestones  428s
Current:  2026-02-27  5/6 milestones  342s
Delta:    +1 milestone, -86s ✅
Regressions: none
```

## File Structure

```
tests/benchmark/
├── run_benchmark.py      # Orchestrator: setup → invoke → verify → record
├── spec.py               # Spec prompt + expected values for verification
├── verify.py             # Milestone checks (independent of agent)
├── compare.py            # Load previous result, print diff
├── results/              # Git-tracked JSON results
└── logs/                 # Git-ignored agent logs
```

## Makefile Targets (root)

```makefile
make benchmark                 # Run with default agent
make benchmark AGENT_CMD=...   # Run with specific agent
make benchmark-compare         # Compare last two runs
make benchmark-verify          # Verify an already-built workspace
```
