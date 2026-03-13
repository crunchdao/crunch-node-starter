"""Tests for PredictionEnsembleStrategy."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from pydantic import BaseModel

from crunch_node.crunch_config import CrunchConfig, EnsembleConfig
from crunch_node.entities.prediction import (
    PredictionRecord,
    PredictionStatus,
    SnapshotRecord,
)
from crunch_node.services.prediction_ensemble import PredictionEnsembleStrategy
from crunch_node.services.prediction_scorer import PredictionScorer

NOW = datetime(2026, 3, 12, 12, 0, 0, tzinfo=UTC)


class StubScore(BaseModel):
    value: float = 0.0


def _scoring_fn(output, gt):
    return StubScore(value=output.value)


def _make_prediction(
    model_id: str,
    input_id: str = "inp1",
    scope_key: str = "BTC-60",
    value: float = 1.0,
) -> PredictionRecord:
    return PredictionRecord(
        id=f"pred_{model_id}_{input_id}_{scope_key}",
        input_id=input_id,
        model_id=model_id,
        prediction_config_id=None,
        scope_key=scope_key,
        scope={"subject": "BTC"},
        status=PredictionStatus.SCORED,
        exec_time_ms=0.0,
        inference_output={"value": value},
        performed_at=NOW,
        resolvable_at=NOW,
    )


def _make_config(ensembles: list[EnsembleConfig] | None = None) -> CrunchConfig:
    return CrunchConfig(
        score_type=StubScore,
        ensembles=ensembles or [],
    )


def _make_strategy(
    config: CrunchConfig,
    predictions: list[PredictionRecord] | None = None,
    snapshots: list[SnapshotRecord] | None = None,
) -> PredictionEnsembleStrategy:
    pred_repo = MagicMock()
    pred_repo.find.return_value = predictions or []
    pred_repo.save = MagicMock()

    snap_repo = MagicMock()
    snap_repo.find.return_value = snapshots or []
    snap_repo.save = MagicMock()

    score_repo = MagicMock()
    score_repo.save = MagicMock()

    input_repo = MagicMock()
    input_repo.get.return_value = MagicMock(raw_data={"value": 1.0})

    scorer = PredictionScorer(
        scoring_function=_scoring_fn,
        input_repository=input_repo,
        prediction_repository=pred_repo,
        score_repository=score_repo,
        snapshot_repository=snap_repo,
        config=config,
    )

    return PredictionEnsembleStrategy(
        config=config,
        scorer=scorer,
        prediction_repository=pred_repo,
        snapshot_repository=snap_repo,
        score_repository=score_repo,
    )


class TestNoEnsemblesConfigured(unittest.TestCase):
    def test_returns_empty(self):
        config = _make_config(ensembles=[])
        strategy = _make_strategy(config)
        result = strategy.compute_ensembles([], NOW)
        self.assertEqual(result, [])


class TestDisabledEnsembleSkipped(unittest.TestCase):
    def test_disabled_ensemble_produces_no_snapshots(self):
        ens = EnsembleConfig(name="disabled_ens", enabled=False)
        config = _make_config(ensembles=[ens])
        predictions = [
            _make_prediction("m1", value=10.0),
            _make_prediction("m2", value=20.0),
        ]
        strategy = _make_strategy(config, predictions=predictions)
        result = strategy.compute_ensembles([], NOW)
        self.assertEqual(result, [])


class TestEnsembleProducesSnapshots(unittest.TestCase):
    def test_basic_flow(self):
        ens = EnsembleConfig(name="main", enabled=True)
        config = _make_config(ensembles=[ens])
        predictions = [
            _make_prediction("m1", value=10.0),
            _make_prediction("m2", value=20.0),
        ]
        strategy = _make_strategy(config, predictions=predictions)
        result = strategy.compute_ensembles([], NOW)

        self.assertEqual(len(result), 1)
        snap = result[0]
        self.assertEqual(snap.model_id, "__ensemble_main__")
        self.assertGreater(snap.prediction_count, 0)


class TestNoModelsAfterFiltering(unittest.TestCase):
    def test_filter_removes_all(self):
        def reject_all(model_id: str, metrics: dict[str, float]) -> bool:
            return False

        ens = EnsembleConfig(name="strict", enabled=True, model_filter=reject_all)
        config = _make_config(ensembles=[ens])
        predictions = [
            _make_prediction("m1", value=10.0),
        ]
        strategy = _make_strategy(config, predictions=predictions)
        result = strategy.compute_ensembles([], NOW)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
