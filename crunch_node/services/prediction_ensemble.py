"""PredictionEnsembleStrategy: compute ensemble predictions and snapshots."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from crunch_node.crunch_config import CrunchConfig, ScoringFunction
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
from crunch_node.services.feed_reader import FeedReader

logger = logging.getLogger(__name__)


class PredictionEnsembleStrategy:
    def __init__(
        self,
        config: CrunchConfig,
        scoring_function: ScoringFunction | Callable,
        feed_reader: FeedReader | None = None,
        input_repository=None,
        prediction_repository=None,
        score_repository=None,
        snapshot_repository=None,
    ):
        self.config = config
        self.scoring_function = scoring_function
        self.feed_reader = feed_reader
        self.input_repository = input_repository
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
                actuals_dict = self._resolve_actuals(ep)
                if actuals_dict is not None:
                    typed_output = self._coerce_output(ep.inference_output)
                    typed_gt = self._coerce_ground_truth(actuals_dict)
                    result = self.scoring_function(typed_output, typed_gt)
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

    def _coerce_output(self, raw: dict[str, Any]) -> BaseModel:
        try:
            return self.config.output_type.model_validate(raw)
        except Exception as exc:
            logger.warning(
                "output_type coercion failed (%s), wrapping raw dict", exc
            )
            try:
                return self.config.output_type.model_construct(**raw)
            except Exception:
                return self.config.output_type()

    def _coerce_ground_truth(self, raw: dict[str, Any]) -> BaseModel:
        gt_type = self.config.get_ground_truth_type()
        try:
            return gt_type.model_validate(raw)
        except Exception as exc:
            logger.warning(
                "ground_truth_type coercion failed (%s), wrapping raw dict", exc
            )
            try:
                return gt_type.model_construct(**raw)
            except Exception:
                return gt_type()

    def _resolve_actuals(self, prediction) -> dict[str, Any] | None:
        if prediction.resolvable_at is None:
            return None

        if prediction.resolvable_at <= prediction.performed_at:
            if self.input_repository is None:
                raise RuntimeError(
                    "resolve_horizon_seconds=0 requires an input_repository "
                    "to look up ground truth from the prediction's input"
                )
            inp = self.input_repository.get(prediction.input_id)
            if inp is None:
                logger.warning(
                    "Input %s not found for prediction %s — skipping",
                    prediction.input_id,
                    prediction.id,
                )
                return None
            return inp.raw_data

        if self.feed_reader is None:
            return None

        scope = prediction.scope or {}
        records = self.feed_reader.fetch_window(
            start=prediction.performed_at,
            end=prediction.resolvable_at,
            source=scope.get("source"),
            subject=scope.get("subject"),
            kind=scope.get("kind"),
            granularity=scope.get("granularity"),
        )

        if not records:
            return None

        staleness_fraction = self.config.max_ground_truth_staleness_fraction
        if staleness_fraction > 0:
            last_record = records[-1]
            horizon_ts = prediction.resolvable_at.timestamp()
            performed_ts = prediction.performed_at.timestamp()
            resolved_ts = last_record.ts_event.timestamp()

            horizon_seconds = horizon_ts - performed_ts
            staleness_seconds = horizon_ts - resolved_ts
            max_staleness = horizon_seconds * staleness_fraction

            if staleness_seconds > max_staleness:
                logger.warning(
                    "Ground truth too stale for prediction %s: "
                    "last record is %.1fs before horizon "
                    "(max allowed: %.1fs = %.0f%% of horizon)",
                    prediction.id,
                    staleness_seconds,
                    max_staleness,
                    staleness_fraction * 100,
                )
                return None

        actuals = self.config.resolve_ground_truth(records, prediction)
        if actuals is None:
            return None
        if isinstance(actuals, BaseModel):
            actuals = actuals.model_dump()
        return actuals
