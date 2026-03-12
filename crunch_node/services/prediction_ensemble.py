"""PredictionEnsembleStrategy: compute ensemble predictions and snapshots."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from crunch_node.crunch_config import CrunchConfig
from crunch_node.entities.prediction import (
    PredictionStatus,
    ScoreRecord,
    SnapshotRecord,
)
from crunch_node.id_prefixes import SCORE_PREFIX, SNAPSHOT_PREFIX
from crunch_node.services.ensemble import (
    apply_model_filter,
    build_ensemble_predictions,
    ensemble_model_id,
    inverse_variance,
    is_ensemble_model,
)

if TYPE_CHECKING:
    from crunch_node.services.prediction_scorer import PredictionScorer

logger = logging.getLogger(__name__)


class PredictionEnsembleStrategy:
    def __init__(
        self,
        config: CrunchConfig,
        scorer: PredictionScorer,
        prediction_repository=None,
        score_repository=None,
        snapshot_repository=None,
    ):
        self.config = config
        self.scorer = scorer
        self.prediction_repository = prediction_repository
        self.score_repository = score_repository
        self.snapshot_repository = snapshot_repository

    def rollback(self) -> None:
        for name, repo in [
            ("prediction", self.prediction_repository),
            ("score", self.score_repository),
            ("snapshot", self.snapshot_repository),
        ]:
            rollback = getattr(repo, "rollback", None)
            if callable(rollback):
                try:
                    rollback()
                except Exception as exc:
                    logger.warning("Rollback failed for %s: %s", name, exc)

    def compute_ensembles(
        self, snapshots: list[SnapshotRecord], now: datetime
    ) -> list[SnapshotRecord]:
        if not self.config.ensembles:
            return []

        from crunch_node.metrics.context import MetricsContext

        predictions = self.prediction_repository.find(status=PredictionStatus.SCORED)

        by_model_preds: dict[str, list[dict[str, Any]]] = {}
        for p in predictions:
            if is_ensemble_model(p.model_id):
                continue
            by_model_preds.setdefault(p.model_id, []).append(
                {
                    "inference_output": p.inference_output,
                    "performed_at": p.performed_at,
                    "scope": p.scope,
                    "input_id": p.input_id,
                    "scope_key": p.scope_key,
                }
            )

        model_metrics: dict[str, dict[str, float]] = {}
        all_snapshots = (
            self.snapshot_repository.find() if self.snapshot_repository else []
        )
        for snap in all_snapshots:
            if not is_ensemble_model(snap.model_id):
                model_metrics[snap.model_id] = {
                    k: float(v)
                    for k, v in snap.result_summary.items()
                    if isinstance(v, (int, float))
                }

        ensemble_predictions_map: dict[str, list[dict[str, Any]]] = {}
        written_snapshots: list[SnapshotRecord] = []

        for ens_config in self.config.ensembles:
            if not ens_config.enabled:
                continue

            filtered_preds = apply_model_filter(
                ens_config.model_filter,
                model_metrics,
                by_model_preds,
            )

            if not filtered_preds:
                logger.info(
                    "Ensemble %r: no models after filtering", ens_config.name
                )
                continue

            strategy = ens_config.strategy
            if strategy is None:
                strategy = inverse_variance

            weights = strategy(model_metrics, filtered_preds)

            ens_preds = build_ensemble_predictions(
                ens_config.name,
                weights,
                filtered_preds,
                now,
            )

            if not ens_preds:
                continue

            for ep in ens_preds:
                self.prediction_repository.save(ep)

            ens_scored: list[ScoreRecord] = []
            for ep in ens_preds:
                actuals_dict = self.scorer._resolve_actuals(ep)
                if actuals_dict is not None:
                    typed_output = self.scorer._coerce_output(ep.inference_output)
                    typed_gt = self.scorer._coerce_ground_truth(actuals_dict)
                    result = self.scorer.scoring_function(typed_output, typed_gt)
                    result_dict = (
                        result.model_dump()
                        if isinstance(result, BaseModel)
                        else result
                    )
                    validated = self.config.score_type.model_validate(result_dict)
                    score = ScoreRecord(
                        id=f"{SCORE_PREFIX}{ep.id}",
                        prediction_id=ep.id,
                        result=validated.model_dump(),
                        success=True,
                        scored_at=now,
                    )
                    if self.score_repository is not None:
                        self.score_repository.save(score)
                    ens_scored.append(score)

            ens_pred_dicts = [
                {
                    "inference_output": ep.inference_output,
                    "performed_at": ep.performed_at,
                    "scope": ep.scope,
                    "input_id": ep.input_id,
                    "scope_key": ep.scope_key,
                }
                for ep in ens_preds
            ]
            ensemble_predictions_map[ens_config.name] = ens_pred_dicts

            if ens_scored and self.snapshot_repository:
                ens_model_id = ensemble_model_id(ens_config.name)
                results = [s.result for s in ens_scored]
                summary = self.config.aggregate_snapshot(results)

                if self.config.metrics:
                    ctx = MetricsContext(
                        model_id=ens_model_id,
                        window_start=min(
                            (s.scored_at for s in ens_scored), default=now
                        ),
                        window_end=now,
                        all_model_predictions=by_model_preds,
                        ensemble_predictions=ensemble_predictions_map,
                    )
                    ens_score_dicts = [
                        {"result": s.result, "scored_at": s.scored_at}
                        for s in ens_scored
                    ]
                    metric_results = self.config.compute_metrics(
                        self.config.metrics,
                        ens_pred_dicts,
                        ens_score_dicts,
                        ctx,
                    )
                    summary.update(metric_results)

                snapshot = SnapshotRecord(
                    id=f"{SNAPSHOT_PREFIX}{ens_model_id}_{now.strftime('%Y%m%d_%H%M%S')}",
                    model_id=ens_model_id,
                    period_start=min(s.scored_at for s in ens_scored),
                    period_end=now,
                    prediction_count=len(ens_scored),
                    result_summary=summary,
                    created_at=now,
                )
                self.snapshot_repository.save(snapshot)
                written_snapshots.append(snapshot)

            logger.info(
                "Ensemble %r: %d models, %d predictions, weights=%s",
                ens_config.name,
                len(weights),
                len(ens_preds),
                {m: round(w, 3) for m, w in weights.items()},
            )

        return written_snapshots

