# ScoreService Decomposition Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Decompose the god-class ScoreService (14 deps, 865 lines) into focused components behind a thin orchestrator.

**Architecture:** Extract LeaderboardService, move checkpoint interval logic into CheckpointService, introduce ScoringStrategy protocol with PredictionScorer implementation, introduce EnsembleStrategy protocol. ScoreService becomes a thin orchestrator (~60 lines) that composes these components.

**Tech Stack:** Python 3.12+, Pydantic, pytest, uv

---

### Task 1: Extract LeaderboardService

**Files:**
- Create: `crunch_node/services/leaderboard.py`
- Test: `tests/test_leaderboard_service.py`
- Modify: `crunch_node/services/score.py` (remove leaderboard methods later in Task 6)

**Step 1: Write the failing test**

```python
"""Tests for LeaderboardService."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from crunch_node.crunch_config import Aggregation, AggregationWindow
from crunch_node.entities.model import Model
from crunch_node.entities.prediction import SnapshotRecord
from crunch_node.services.leaderboard import LeaderboardService


class MemSnapshotRepository:
    def __init__(self, snapshots: list | None = None) -> None:
        self.snapshots: list = list(snapshots or [])

    def save(self, record) -> None:
        self.snapshots.append(record)

    def find(self, *, model_id=None, since=None, until=None, limit=None) -> list:
        results = list(self.snapshots)
        if model_id is not None:
            results = [s for s in results if s.model_id == model_id]
        return results


class MemModelRepository:
    def __init__(self, models: dict | None = None) -> None:
        self.models = models or {}

    def fetch_all(self) -> dict[str, Model]:
        return self.models


class MemLeaderboardRepository:
    def __init__(self) -> None:
        self.latest: Any = None

    def save(self, entries: Any, meta: Any = None) -> None:
        self.latest = {"entries": entries, "meta": meta or {}}

    def get_latest(self) -> Any:
        return self.latest


now = datetime.now(UTC)


class TestLeaderboardService(unittest.TestCase):
    def _make_snapshot(self, model_id: str, value: float, age_minutes: int = 5) -> SnapshotRecord:
        return SnapshotRecord(
            id=f"SNAP_{model_id}_{age_minutes}",
            model_id=model_id,
            period_start=now - timedelta(minutes=age_minutes + 1),
            period_end=now - timedelta(minutes=age_minutes),
            prediction_count=1,
            result_summary={"value": value},
            created_at=now - timedelta(minutes=age_minutes),
        )

    def test_rebuild_ranks_models_descending(self):
        snapshots = [
            self._make_snapshot("m1", 0.3),
            self._make_snapshot("m2", 0.7),
        ]
        models = {
            "m1": Model(id="m1", name="model-one", player_id="p1", player_name="alice", deployment_identifier="d1"),
            "m2": Model(id="m2", name="model-two", player_id="p2", player_name="bob", deployment_identifier="d2"),
        }
        leaderboard_repo = MemLeaderboardRepository()
        service = LeaderboardService(
            snapshot_repository=MemSnapshotRepository(snapshots),
            model_repository=MemModelRepository(models),
            leaderboard_repository=leaderboard_repo,
            aggregation=Aggregation(),
        )

        service.rebuild()

        entries = leaderboard_repo.latest["entries"]
        self.assertEqual(entries[0]["model_id"], "m2")
        self.assertEqual(entries[0]["rank"], 1)
        self.assertEqual(entries[1]["model_id"], "m1")
        self.assertEqual(entries[1]["rank"], 2)

    def test_rebuild_ascending_ranking(self):
        snapshots = [
            self._make_snapshot("m1", 0.3),
            self._make_snapshot("m2", 0.7),
        ]
        aggregation = Aggregation(
            windows={"loss": AggregationWindow(hours=24)},
            ranking_key="loss",
            ranking_direction="asc",
        )
        service = LeaderboardService(
            snapshot_repository=MemSnapshotRepository(snapshots),
            model_repository=MemModelRepository(),
            leaderboard_repository=MemLeaderboardRepository(),
            aggregation=aggregation,
        )

        service.rebuild()

    def test_rebuild_with_no_snapshots(self):
        leaderboard_repo = MemLeaderboardRepository()
        service = LeaderboardService(
            snapshot_repository=MemSnapshotRepository(),
            model_repository=MemModelRepository(),
            leaderboard_repository=leaderboard_repo,
            aggregation=Aggregation(),
        )

        service.rebuild()

        entries = leaderboard_repo.latest["entries"]
        self.assertEqual(entries, [])
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_leaderboard_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crunch_node.services.leaderboard'`

**Step 3: Write minimal implementation**

Move `_rebuild_leaderboard`, `_aggregate_from_snapshots`, `_rank`, and `_ensure_utc` from `crunch_node/services/score.py` into `crunch_node/services/leaderboard.py`:

```python
"""Leaderboard service: aggregate snapshots into ranked model entries."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from crunch_node.crunch_config import Aggregation
from crunch_node.entities.prediction import SnapshotRecord


class LeaderboardService:
    def __init__(
        self,
        snapshot_repository,
        model_repository,
        leaderboard_repository,
        aggregation: Aggregation,
    ):
        self.snapshot_repository = snapshot_repository
        self.model_repository = model_repository
        self.leaderboard_repository = leaderboard_repository
        self.aggregation = aggregation
        self.logger = logging.getLogger(__name__)

    def rebuild(self) -> None:
        models = self.model_repository.fetch_all()
        snapshots = self.snapshot_repository.find() if self.snapshot_repository else []
        aggregated = self._aggregate_from_snapshots(snapshots, models)
        ranked = self._rank(aggregated)
        self.leaderboard_repository.save(
            ranked,
            meta={"generated_by": "crunch_node.leaderboard_service"},
        )

    def _aggregate_from_snapshots(
        self, snapshots: list[SnapshotRecord], models: dict
    ) -> list[dict[str, Any]]:
        now = datetime.now(UTC)

        by_model: dict[str, list[SnapshotRecord]] = {}
        for snap in snapshots:
            by_model.setdefault(snap.model_id, []).append(snap)

        entries: list[dict[str, Any]] = []
        for model_id, model_snapshots in by_model.items():
            metrics: dict[str, float] = {}

            for window_name, window in self.aggregation.windows.items():
                cutoff = now - timedelta(hours=window.hours)
                window_snaps = [
                    s
                    for s in model_snapshots
                    if self._ensure_utc(s.period_end) >= cutoff
                ]
                if window_snaps:
                    vals = [
                        float(s.result_summary.get(self.aggregation.value_field, 0))
                        for s in window_snaps
                    ]
                    metrics[window_name] = sum(vals) / len(vals)
                else:
                    metrics[window_name] = 0.0

            latest_snap = max(
                model_snapshots, key=lambda s: self._ensure_utc(s.period_end)
            )
            for key, value in latest_snap.result_summary.items():
                if key not in metrics:
                    try:
                        metrics[key] = float(value)
                    except (ValueError, TypeError):
                        pass

            model = models.get(model_id)
            entry: dict[str, Any] = {
                "model_id": model_id,
                "score": {
                    "metrics": metrics,
                    "ranking": {
                        "key": self.aggregation.ranking_key,
                        "value": metrics.get(self.aggregation.ranking_key, 0.0),
                        "direction": self.aggregation.ranking_direction,
                    },
                },
            }
            if model:
                entry["model_name"] = model.name
                entry["cruncher_name"] = model.player_name
            entries.append(entry)

        return entries

    def _rank(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        key = self.aggregation.ranking_key
        reverse = self.aggregation.ranking_direction == "desc"

        def sort_key(e: dict[str, Any]) -> float:
            score = e.get("score")
            if not isinstance(score, dict):
                return float("-inf")
            try:
                return float((score.get("metrics") or {}).get(key, 0.0))
            except Exception:
                return float("-inf")

        ranked = sorted(entries, key=sort_key, reverse=reverse)
        for idx, entry in enumerate(ranked, start=1):
            entry["rank"] = idx
        return ranked

    @staticmethod
    def _ensure_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_leaderboard_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/leaderboard.py tests/test_leaderboard_service.py
git commit -m "Extract LeaderboardService from ScoreService"
```

---

### Task 2: Move checkpoint interval logic into CheckpointService

**Files:**
- Modify: `crunch_node/services/checkpoint.py`
- Create: `tests/test_checkpoint_interval.py`

**Step 1: Write the failing test**

```python
"""Tests for CheckpointService.maybe_checkpoint interval logic."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from crunch_node.services.checkpoint import CheckpointService, EmissionConfig


class MemCheckpointRepository:
    def __init__(self) -> None:
        self.checkpoints: list = []

    def save(self, record) -> None:
        self.checkpoints.append(record)

    def get_latest(self) -> Any:
        return self.checkpoints[-1] if self.checkpoints else None

    def find(self, **kwargs) -> list:
        return list(self.checkpoints)

    def update_merkle_root(self, checkpoint_id: str, merkle_root: str) -> None:
        pass


class MemSnapshotRepository:
    def __init__(self, snapshots: list | None = None) -> None:
        self.snapshots = list(snapshots or [])

    def find(self, *, since=None, until=None, **kwargs) -> list:
        return list(self.snapshots)


class MemModelRepository:
    def fetch_all(self) -> dict:
        return {}


def _noop_emission(entries, *, crunch_pubkey="", compute_provider=None, data_provider=None):
    return MagicMock(id="EMI_1")


class TestMaybeCheckpoint(unittest.TestCase):
    def test_maybe_checkpoint_skips_before_interval(self):
        service = CheckpointService(
            snapshot_repository=MemSnapshotRepository(),
            checkpoint_repository=MemCheckpointRepository(),
            model_repository=MemModelRepository(),
            emission=EmissionConfig(build_emission=_noop_emission),
            interval_seconds=3600,
        )
        now = datetime.now(UTC)
        result = service.maybe_checkpoint(now)
        self.assertIsNone(result)

    def test_maybe_checkpoint_creates_after_interval(self):
        from crunch_node.entities.prediction import SnapshotRecord

        snap = SnapshotRecord(
            id="SNAP_1",
            model_id="m1",
            period_start=datetime.now(UTC) - timedelta(hours=2),
            period_end=datetime.now(UTC) - timedelta(hours=1),
            prediction_count=5,
            result_summary={"value": 0.5},
            created_at=datetime.now(UTC),
        )

        service = CheckpointService(
            snapshot_repository=MemSnapshotRepository([snap]),
            checkpoint_repository=MemCheckpointRepository(),
            model_repository=MemModelRepository(),
            emission=EmissionConfig(build_emission=_noop_emission),
            interval_seconds=60,
        )
        now = datetime.now(UTC)
        result = service.maybe_checkpoint(now)
        self.assertIsNotNone(result)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_checkpoint_interval.py -v`
Expected: FAIL with `AttributeError: 'CheckpointService' object has no attribute 'maybe_checkpoint'`

**Step 3: Add `maybe_checkpoint` method to CheckpointService**

Add to `crunch_node/services/checkpoint.py`:

```python
# Add to __init__:
self._last_checkpoint_at: datetime | None = None

def maybe_checkpoint(self, now: datetime) -> CheckpointRecord | None:
    if self._last_checkpoint_at is None:
        latest = self.checkpoint_repository.get_latest()
        self._last_checkpoint_at = (
            self._ensure_utc(latest.period_end)
            if latest
            else datetime.min.replace(tzinfo=UTC)
        )

    elapsed = (now - self._last_checkpoint_at).total_seconds()
    if elapsed < self.interval_seconds:
        return None

    try:
        checkpoint = self.create_checkpoint()
        if checkpoint is not None:
            self._last_checkpoint_at = now
        return checkpoint
    except Exception as exc:
        self.logger.exception("Checkpoint creation failed: %s", exc)
        return None

@staticmethod
def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_checkpoint_interval.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/checkpoint.py tests/test_checkpoint_interval.py
git commit -m "Add maybe_checkpoint interval logic to CheckpointService"
```

---

### Task 3: Define ScoringStrategy protocol

**Files:**
- Create: `crunch_node/services/scoring_strategy.py`

**Step 1: Write the protocol**

```python
"""Scoring strategy protocol for pluggable snapshot production."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from crunch_node.entities.prediction import SnapshotRecord


class ScoringStrategy(Protocol):
    def produce_snapshots(self, now: datetime) -> list[SnapshotRecord]: ...
    def rollback(self) -> None: ...
```

**Step 2: Commit**

```bash
git add crunch_node/services/scoring_strategy.py
git commit -m "Add ScoringStrategy protocol"
```

---

### Task 4: Extract PredictionScorer

This is the largest task. It moves all prediction-specific scoring logic out of ScoreService.

**Files:**
- Create: `crunch_node/services/prediction_scorer.py`
- Create: `tests/test_prediction_scorer.py`

**Step 1: Write the failing tests**

Port the core tests from `tests/test_node_template_score_service.py` to target `PredictionScorer` directly. Reuse the same Mem* repositories and helpers. Key tests:

```python
"""Tests for PredictionScorer."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.feed_record import FeedRecord
from crunch_node.entities.model import Model
from crunch_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    ScoreRecord,
)
from crunch_node.services.prediction_scorer import PredictionScorer


# Reuse the same Mem* repositories from test_node_template_score_service.py
# (copy MemInputRepository, MemPredictionRepository, MemScoreRepository,
#  MemSnapshotRepository, FakeFeedReader, _make_input, _make_prediction,
#  _make_feed_records)
# ... (same helpers as in test_node_template_score_service.py)


def _build_scorer(*, inputs=None, predictions=None, feed_records=None, config=None):
    return PredictionScorer(
        scoring_function=lambda pred, act: {
            "value": 0.5,
            "success": True,
            "failed_reason": None,
        },
        feed_reader=FakeFeedReader(records=feed_records or []),
        input_repository=MemInputRepository(inputs or []),
        prediction_repository=MemPredictionRepository(predictions or []),
        score_repository=MemScoreRepository(),
        snapshot_repository=MemSnapshotRepository(),
        config=config,
    )


class TestPredictionScorer(unittest.TestCase):
    def test_produce_snapshots_scores_and_aggregates(self):
        scorer = _build_scorer(
            inputs=[_make_input()],
            predictions=[_make_prediction()],
            feed_records=_make_feed_records(),
        )
        snapshots = scorer.produce_snapshots(datetime.now(UTC))
        self.assertEqual(len(snapshots), 1)

    def test_produce_snapshots_returns_empty_when_no_predictions(self):
        scorer = _build_scorer()
        snapshots = scorer.produce_snapshots(datetime.now(UTC))
        self.assertEqual(snapshots, [])

    def test_validate_scoring_io_passes_compatible(self):
        scorer = _build_scorer()
        scorer.validate_scoring_io()

    def test_rollback_delegates_to_repos(self):
        scorer = _build_scorer()
        scorer.rollback()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prediction_scorer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'crunch_node.services.prediction_scorer'`

**Step 3: Write PredictionScorer implementation**

Move from `crunch_node/services/score.py` into `crunch_node/services/prediction_scorer.py`:
- `_score_predictions` → becomes internal, called by `produce_snapshots`
- `_resolve_actuals`
- `_coerce_output`
- `_coerce_ground_truth`
- `_write_snapshots`
- `validate_scoring_io`
- `detect_scoring_stub` (static)

```python
"""Prediction-based scoring strategy: resolve actuals → score → aggregate snapshots."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from crunch_node.crunch_config import CrunchConfig, ScoringFunction
from crunch_node.entities.prediction import (
    PredictionStatus,
    ScoreRecord,
    SnapshotRecord,
)
from crunch_node.services.feed_reader import FeedReader


class PredictionScorer:
    def __init__(
        self,
        scoring_function: ScoringFunction | Callable,
        feed_reader: FeedReader | None = None,
        input_repository=None,
        prediction_repository=None,
        score_repository=None,
        snapshot_repository=None,
        config: CrunchConfig | None = None,
    ):
        self.scoring_function = scoring_function
        self.feed_reader = feed_reader
        self.input_repository = input_repository
        self.prediction_repository = prediction_repository
        self.score_repository = score_repository
        self.snapshot_repository = snapshot_repository
        self.config = config or CrunchConfig()
        self.logger = logging.getLogger(__name__)

    def produce_snapshots(self, now: datetime) -> list[SnapshotRecord]:
        scored = self._score_predictions(now)
        if not scored:
            return []
        return self._write_snapshots(scored, now)

    def rollback(self) -> None:
        for name, repo in [
            ("input", self.input_repository),
            ("prediction", self.prediction_repository),
            ("score", self.score_repository),
            ("snapshot", self.snapshot_repository),
        ]:
            rollback = getattr(repo, "rollback", None)
            if callable(rollback):
                try:
                    rollback()
                except Exception as exc:
                    self.logger.warning("Rollback failed for %s: %s", name, exc)

    # Move _resolve_actuals, _coerce_output, _coerce_ground_truth,
    # _score_predictions, _write_snapshots, validate_scoring_io,
    # detect_scoring_stub verbatim from score.py
    # (identical logic, just a new home)
    ...
```

The full implementation is a direct move of the methods from `score.py` — no logic changes.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prediction_scorer.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/prediction_scorer.py tests/test_prediction_scorer.py
git commit -m "Extract PredictionScorer from ScoreService"
```

---

### Task 5: Define EnsembleStrategy protocol and extract PredictionEnsembleStrategy

**Files:**
- Add to: `crunch_node/services/scoring_strategy.py`
- Create: `crunch_node/services/prediction_ensemble.py`
- Create: `tests/test_prediction_ensemble.py`

**Step 1: Add EnsembleStrategy protocol**

Add to `crunch_node/services/scoring_strategy.py`:

```python
class EnsembleStrategy(Protocol):
    def compute_ensembles(
        self, snapshots: list[SnapshotRecord], now: datetime
    ) -> list[SnapshotRecord]: ...
```

**Step 2: Write the failing test**

```python
"""Tests for PredictionEnsembleStrategy."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from crunch_node.crunch_config import CrunchConfig, EnsembleConfig
from crunch_node.entities.prediction import (
    PredictionRecord,
    ScoreRecord,
    SnapshotRecord,
)
from crunch_node.services.prediction_ensemble import PredictionEnsembleStrategy


# ... Mem* repos, helpers ...

class TestPredictionEnsembleStrategy(unittest.TestCase):
    def test_no_ensembles_configured_returns_empty(self):
        strategy = PredictionEnsembleStrategy(
            config=CrunchConfig(),
            prediction_repository=MemPredictionRepository(),
            score_repository=MemScoreRepository(),
            snapshot_repository=MemSnapshotRepository(),
            scoring_function=lambda p, g: {"value": 0.0, "success": True, "failed_reason": None},
        )
        result = strategy.compute_ensembles([], datetime.now(UTC))
        self.assertEqual(result, [])
```

**Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_prediction_ensemble.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 4: Write PredictionEnsembleStrategy**

Move `_compute_ensembles` logic from `score.py` into `PredictionEnsembleStrategy.compute_ensembles()`. Crucially, refactor the inline scoring loop to reuse `PredictionScorer`'s scoring method or extract a shared `score_single_prediction` function. The key deduplication: ensemble predictions flow through the same score-and-persist logic as regular predictions.

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_prediction_ensemble.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add crunch_node/services/scoring_strategy.py crunch_node/services/prediction_ensemble.py tests/test_prediction_ensemble.py
git commit -m "Extract PredictionEnsembleStrategy with EnsembleStrategy protocol"
```

---

### Task 6: Rewrite ScoreService as thin orchestrator

**Files:**
- Modify: `crunch_node/services/score.py`
- Create: `tests/test_score_orchestrator.py`

**Step 1: Write the failing test**

```python
"""Tests for ScoreService orchestrator."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock

from crunch_node.services.score import ScoreService


class TestScoreOrchestrator(unittest.TestCase):
    def test_score_and_snapshot_calls_pipeline(self):
        strategy = MagicMock()
        strategy.produce_snapshots.return_value = [MagicMock()]

        ensemble = MagicMock()
        ensemble.compute_ensembles.return_value = []

        leaderboard = MagicMock()
        merkle = MagicMock()
        checkpoint = MagicMock()

        service = ScoreService(
            scoring_strategy=strategy,
            ensemble_strategy=ensemble,
            leaderboard_service=leaderboard,
            merkle_service=merkle,
            checkpoint_service=checkpoint,
            score_interval_seconds=60,
        )

        result = service.score_and_snapshot()

        self.assertTrue(result)
        strategy.produce_snapshots.assert_called_once()
        ensemble.compute_ensembles.assert_called_once()
        merkle.commit_cycle.assert_called_once()
        leaderboard.rebuild.assert_called_once()
        checkpoint.maybe_checkpoint.assert_called_once()

    def test_returns_false_when_no_snapshots(self):
        strategy = MagicMock()
        strategy.produce_snapshots.return_value = []

        service = ScoreService(
            scoring_strategy=strategy,
            ensemble_strategy=None,
            leaderboard_service=MagicMock(),
            merkle_service=None,
            checkpoint_service=None,
            score_interval_seconds=60,
        )

        result = service.score_and_snapshot()
        self.assertFalse(result)


class TestScoreOrchestratorRunLoop(unittest.IsolatedAsyncioTestCase):
    async def test_rollback_on_exception(self):
        strategy = MagicMock()
        strategy.produce_snapshots.side_effect = RuntimeError("boom")

        service = ScoreService(
            scoring_strategy=strategy,
            ensemble_strategy=None,
            leaderboard_service=MagicMock(),
            merkle_service=None,
            checkpoint_service=None,
            score_interval_seconds=60,
        )
        service.stop_event.set()

        with self.assertLogs(level="ERROR"):
            await service.run()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_score_orchestrator.py -v`
Expected: FAIL (ScoreService constructor signature doesn't match)

**Step 3: Rewrite ScoreService**

Replace `crunch_node/services/score.py` with the thin orchestrator:

```python
"""Score service: thin orchestrator for the scoring pipeline."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from crunch_node.services.scoring_strategy import EnsembleStrategy, ScoringStrategy


class ScoreService:
    def __init__(
        self,
        scoring_strategy: ScoringStrategy,
        ensemble_strategy: EnsembleStrategy | None,
        leaderboard_service,
        merkle_service=None,
        checkpoint_service=None,
        score_interval_seconds: int = 60,
    ):
        self.scoring_strategy = scoring_strategy
        self.ensemble_strategy = ensemble_strategy
        self.leaderboard_service = leaderboard_service
        self.merkle_service = merkle_service
        self.checkpoint_service = checkpoint_service
        self.score_interval_seconds = score_interval_seconds
        self.logger = logging.getLogger(__name__)
        self.stop_event = asyncio.Event()

    def score_and_snapshot(self) -> bool:
        now = datetime.now(UTC)

        snapshots = self.scoring_strategy.produce_snapshots(now)
        if not snapshots:
            self.logger.info("No snapshots produced this cycle")
            return False

        if self.ensemble_strategy:
            ensemble_snapshots = self.ensemble_strategy.compute_ensembles(snapshots, now)
            snapshots += ensemble_snapshots

        if self.merkle_service and snapshots:
            try:
                self.merkle_service.commit_cycle(snapshots, now)
            except Exception as exc:
                self.logger.warning("Merkle cycle commit failed: %s", exc)

        self.leaderboard_service.rebuild()

        if self.checkpoint_service:
            self.checkpoint_service.maybe_checkpoint(now)

        return True

    async def run(self) -> None:
        self.logger.info(
            "score service started (score_interval=%ds)",
            self.score_interval_seconds,
        )
        while not self.stop_event.is_set():
            try:
                self.score_and_snapshot()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.exception("score loop error: %s", exc)
                self._rollback()
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=self.score_interval_seconds
                )
            except TimeoutError:
                pass

    async def shutdown(self) -> None:
        self.stop_event.set()

    def _rollback(self) -> None:
        rollback = getattr(self.scoring_strategy, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception as exc:
                self.logger.warning("Strategy rollback failed: %s", exc)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_score_orchestrator.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add crunch_node/services/score.py tests/test_score_orchestrator.py
git commit -m "Rewrite ScoreService as thin orchestrator"
```

---

### Task 7: Update score_worker.py wiring

**Files:**
- Modify: `crunch_node/workers/score_worker.py`

**Step 1: Rewrite `build_service()`**

```python
from crunch_node.services.leaderboard import LeaderboardService
from crunch_node.services.prediction_scorer import PredictionScorer
from crunch_node.services.prediction_ensemble import PredictionEnsembleStrategy


def build_service() -> ScoreService:
    extension_settings = ExtensionSettings.from_env()
    runtime_settings = RuntimeSettings.from_env()
    config = load_config()

    session = create_session()
    snapshot_repo = DBSnapshotRepository(session)
    model_repo = DBModelRepository(session)

    # Scoring strategy
    if config.build_score_snapshots is not None:
        scoring_strategy = config.build_score_snapshots(
            session=session, config=config, snapshot_repository=snapshot_repo
        )
    else:
        if config.scoring_function is not None:
            scoring_function = config.scoring_function
        else:
            scoring_function = resolve_callable(
                extension_settings.scoring_function,
                required_params=("prediction", "ground_truth"),
            )

        scoring_strategy = PredictionScorer(
            scoring_function=scoring_function,
            feed_reader=FeedReader.from_env(),
            input_repository=DBInputRepository(session),
            prediction_repository=DBPredictionRepository(session),
            score_repository=DBScoreRepository(session),
            snapshot_repository=snapshot_repo,
            config=config,
        )

    # Ensemble strategy
    ensemble_strategy = None
    if config.ensembles and isinstance(scoring_strategy, PredictionScorer):
        ensemble_strategy = PredictionEnsembleStrategy(
            config=config,
            prediction_repository=scoring_strategy.prediction_repository,
            score_repository=scoring_strategy.score_repository,
            snapshot_repository=scoring_strategy.snapshot_repository,
            scoring_function=scoring_strategy.scoring_function,
            feed_reader=scoring_strategy.feed_reader,
            input_repository=scoring_strategy.input_repository,
        )

    # Leaderboard
    leaderboard_service = LeaderboardService(
        snapshot_repository=snapshot_repo,
        model_repository=model_repo,
        leaderboard_repository=DBLeaderboardRepository(session),
        aggregation=config.aggregation,
    )

    # Merkle
    merkle_service = MerkleService(
        merkle_cycle_repository=DBMerkleCycleRepository(session),
        merkle_node_repository=DBMerkleNodeRepository(session),
    )

    # Checkpoint
    checkpoint_service = CheckpointService(
        snapshot_repository=snapshot_repo,
        checkpoint_repository=DBCheckpointRepository(session),
        model_repository=model_repo,
        emission=EmissionConfig(
            build_emission=config.build_emission,
            crunch_pubkey=config.crunch_pubkey,
            compute_provider=config.compute_provider,
            data_provider=config.data_provider,
        ),
        interval_seconds=runtime_settings.checkpoint_interval_seconds,
        merkle_service=merkle_service,
        ranking_key=config.aggregation.ranking_key,
        ranking_direction=config.aggregation.ranking_direction,
    )

    return ScoreService(
        scoring_strategy=scoring_strategy,
        ensemble_strategy=ensemble_strategy,
        leaderboard_service=leaderboard_service,
        merkle_service=merkle_service,
        checkpoint_service=checkpoint_service,
        score_interval_seconds=runtime_settings.score_interval_seconds or 60,
    )


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("score worker bootstrap")

    service = build_service()

    if isinstance(service.scoring_strategy, PredictionScorer):
        service.scoring_strategy.validate_scoring_io()

    await service.run()
```

**Step 2: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: PASS (new tests pass, old tests may need updating in Task 8)

**Step 3: Commit**

```bash
git add crunch_node/workers/score_worker.py
git commit -m "Update score_worker wiring for decomposed services"
```

---

### Task 8: Update existing tests

**Files:**
- Modify: `tests/test_node_template_score_service.py`
- Modify: `tests/test_multi_metric_scoring.py`

**Step 1: Update test_node_template_score_service.py**

Update `_build_service()` and tests to use the new constructor. Tests that tested internal methods (`_coerce_output`, `_score_predictions`) should target `PredictionScorer` instead. Tests for `score_and_snapshot` should construct the full orchestrator with a `PredictionScorer` strategy.

**Step 2: Update test_multi_metric_scoring.py**

Update to call `PredictionScorer` methods directly instead of `ScoreService._write_snapshots`.

**Step 3: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add tests/test_node_template_score_service.py tests/test_multi_metric_scoring.py
git commit -m "Update existing tests for decomposed ScoreService"
```

---

### Task 9: Clean up old code

**Files:**
- Modify: `crunch_node/services/score.py` (remove any leftover dead code)
- Verify: no other files import the old ScoreService methods

**Step 1: Grep for stale imports**

Run: `uv run rg "from crunch_node.services.score import" --type py`

Verify only `ScoreService` is imported (not internal methods like `_rank` or `_coerce_output`).

**Step 2: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL PASS

**Step 3: Commit**

```bash
git add -u
git commit -m "Remove dead code from old ScoreService"
```
