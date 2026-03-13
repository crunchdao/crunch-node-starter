# Score Service Decomposition

## Problem

`ScoreService` is a god class: 14 constructor dependencies, 865 lines, 5 distinct responsibilities (scoring, snapshots, ensembles, leaderboard, checkpointing). The ensemble computation duplicates scoring/snapshot logic inline. The worker wiring (`build_service()`) is complex because it assembles everything into one flat object.

## Design

Decompose into focused components behind a thin orchestrator.

### ScoringStrategy protocol

```python
class ScoringStrategy(Protocol):
    def produce_snapshots(self, now: datetime) -> list[SnapshotRecord]: ...
    def rollback(self) -> None: ...
```

Two implementations:
- **PredictionScorer** — resolves actuals, coerces types, calls scoring function, aggregates into snapshots, computes metrics. Owns: `scoring_function`, `feed_reader`, `input_repository`, `prediction_repository`, `score_repository`, `snapshot_repository`, config coercion/metrics logic. Also owns `validate_scoring_io()` and `detect_scoring_stub()`.
- **Trading pack's scorer** — already exists as `build_score_snapshots` factory. Wraps to conform to protocol.

### EnsembleStrategy protocol

```python
class EnsembleStrategy(Protocol):
    def compute_ensembles(
        self, snapshots: list[SnapshotRecord], now: datetime
    ) -> list[SnapshotRecord]: ...
```

Ensembling is a general concept that applies differently per scoring strategy:
- **Prediction ensembles:** weighted average of prediction outputs, scored through the same pipeline (deduplicating the current inline scoring in `_compute_ensembles`).
- **Trading ensembles:** select best-performing model per asset to compose a virtual portfolio.

Each pack/strategy provides its own ensemble implementation.

### LeaderboardService

Extracted from ScoreService. Contains `_aggregate_from_snapshots`, `_rank`, `_ensure_utc`.

```python
class LeaderboardService:
    def __init__(self, snapshot_repository, model_repository, leaderboard_repository, aggregation): ...
    def rebuild(self) -> None: ...
```

Dependencies: 3 repositories + aggregation config (down from 6 repos + full config).

### CheckpointService gains interval logic

`_maybe_checkpoint()` interval tracking (`_last_checkpoint_at`, elapsed check) moves into `CheckpointService.maybe_checkpoint(now)`. It already owns creation and has `interval_seconds`.

### ScoreService (thin orchestrator)

```python
class ScoreService:
    def __init__(
        self,
        scoring_strategy: ScoringStrategy,
        ensemble_strategy: EnsembleStrategy | None,
        leaderboard_service: LeaderboardService,
        merkle_service: MerkleService | None,
        checkpoint_service: CheckpointService | None,
        score_interval_seconds: int,
    ): ...

    def score_and_snapshot(self) -> bool:
        now = datetime.now(UTC)
        snapshots = self.scoring_strategy.produce_snapshots(now)
        if not snapshots:
            return False
        if self.ensemble_strategy:
            snapshots += self.ensemble_strategy.compute_ensembles(snapshots, now)
        if self.merkle_service:
            self.merkle_service.commit_cycle(snapshots, now)
        self.leaderboard_service.rebuild()
        self.checkpoint_service.maybe_checkpoint(now)
        return True
```

Constructor: 6 dependencies (down from 14). ~60 lines (down from 865).
Retains: `run()` async loop, `shutdown()`, rollback delegation.

### What moves where

| Current location | Destination |
|---|---|
| `_score_predictions()` | `PredictionScorer` |
| `_resolve_actuals()` | `PredictionScorer` |
| `_coerce_output/ground_truth()` | `PredictionScorer` |
| `_write_snapshots()` | `PredictionScorer` |
| `_compute_ensembles()` | `PredictionEnsembleStrategy` (deduplicated) |
| `_rebuild_leaderboard()` | `LeaderboardService` |
| `_aggregate_from_snapshots()` | `LeaderboardService` |
| `_rank()` | `LeaderboardService` |
| `_maybe_checkpoint()` interval logic | `CheckpointService` |
| `validate_scoring_io()` | `PredictionScorer` |
| `detect_scoring_stub()` | `PredictionScorer` |

### Worker wiring

`score_worker.py` assembles 4-5 focused components instead of 14 flat dependencies:

```python
def build_service() -> ScoreService:
    config = load_config()
    session = create_session()

    if config.build_score_snapshots is not None:
        strategy = config.build_score_snapshots(session=session, config=config, ...)
    else:
        strategy = PredictionScorer(...)

    return ScoreService(
        scoring_strategy=strategy,
        ensemble_strategy=...,
        leaderboard_service=LeaderboardService(...),
        merkle_service=MerkleService(...),
        checkpoint_service=CheckpointService(...),
        score_interval_seconds=runtime.score_interval_seconds,
    )
```

`validate_scoring_io()` called conditionally: `if isinstance(strategy, PredictionScorer): strategy.validate_scoring_io()`
