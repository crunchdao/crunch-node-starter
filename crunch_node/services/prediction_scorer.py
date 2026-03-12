"""PredictionScorer: score predictions and produce snapshots."""

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
from crunch_node.services.feed_reader import FeedReader

logger = logging.getLogger(__name__)


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
                    logger.warning("Rollback failed for %s: %s", name, exc)

    def validate_scoring_io(self) -> None:
        """Dry-run the scoring function with default config types at startup.

        Catches field-name mismatches (e.g. scoring reads ``prediction.order_type``
        but ``output_type`` only defines ``value``) before any real predictions
        are scored.  Raises on hard errors; logs warnings on soft issues.
        """
        output_type = self.config.output_type
        ground_truth_type = self.config.get_ground_truth_type()

        try:
            sample_output = output_type()
        except Exception as exc:
            raise RuntimeError(
                f"Cannot construct a default {output_type.__name__}: {exc}. "
                f"Ensure all fields have defaults or the model_config allows it."
            ) from exc

        try:
            sample_gt = ground_truth_type()
        except Exception as exc:
            logger.warning(
                "Cannot construct a default GroundTruth (%s): %s — "
                "scoring dry-run skipped (ground truth requires runtime data)",
                ground_truth_type.__name__,
                exc,
            )
            return

        try:
            result = self.scoring_function(sample_output, sample_gt)
        except KeyError as exc:
            raise RuntimeError(
                f"Scoring function raised KeyError({exc}) when called with default "
                f"{output_type.__name__} and default {ground_truth_type.__name__}. "
                f"Ensure the scoring function reads attributes defined on these types."
            ) from exc
        except AttributeError as exc:
            raise RuntimeError(
                f"Scoring function raised AttributeError({exc}) — it may be "
                f"using dict access (.get/[]) instead of attribute access. "
                f"Scoring functions now receive typed Pydantic objects."
            ) from exc
        except Exception as exc:
            logger.warning(
                "Scoring dry-run raised %s: %s — this may be OK if the function "
                "requires real data, but check field names match output_type",
                type(exc).__name__,
                exc,
            )
            return

        result_dict = result.model_dump() if isinstance(result, BaseModel) else result
        try:
            self.config.score_type.model_validate(result_dict)
        except Exception as exc:
            raise RuntimeError(
                f"Scoring function returned {result!r} which does not match "
                f"{self.config.score_type.__name__}: {exc}"
            ) from exc

        logger.info(
            "Scoring IO validation passed: %s → scoring → %s",
            output_type.__name__,
            self.config.score_type.__name__,
        )

    @staticmethod
    def detect_scoring_stub(
        scoring_function: ScoringFunction | Callable,
    ) -> tuple[bool, str]:
        """Probe the scoring function with varied inputs to detect stubs.

        Returns (is_stub, reason). A function that returns identical scores
        for significantly different inputs is likely a placeholder.
        Uses raw dicts for probing since concrete types aren't known here.
        """
        test_cases = [
            (
                {"value": 1.0},
                {
                    "entry_price": 40000,
                    "resolved_price": 40100,
                    "profit": 0.0025,
                    "direction_up": True,
                },
            ),
            (
                {"value": -1.0},
                {
                    "entry_price": 40000,
                    "resolved_price": 39900,
                    "profit": -0.0025,
                    "direction_up": False,
                },
            ),
            (
                {"value": 0.5},
                {
                    "entry_price": 40000,
                    "resolved_price": 40500,
                    "profit": 0.0125,
                    "direction_up": True,
                },
            ),
        ]

        results = []
        for pred, gt in test_cases:
            try:
                result = scoring_function(pred, gt)
                if isinstance(result, BaseModel):
                    results.append(getattr(result, "value", 0.0))
                else:
                    results.append(result.get("value", 0.0))
            except Exception:
                return False, "scoring function raised an exception during probe"

        if len(set(results)) <= 1:
            return True, (
                f"Scoring function returns identical value ({results[0]}) for all "
                f"test inputs. This looks like a stub — implement real scoring logic."
            )

        return False, "ok"

    def _coerce_output(self, raw: dict[str, Any]) -> BaseModel:
        """Parse a raw inference_output dict into a typed ``output_type`` instance."""
        try:
            return self.config.output_type.model_validate(raw)
        except Exception as exc:
            logger.warning(
                "output_type coercion failed (%s), wrapping raw dict",
                exc,
            )
            try:
                return self.config.output_type.model_construct(**raw)
            except Exception:
                return self.config.output_type()

    def _coerce_ground_truth(self, raw: dict[str, Any]) -> BaseModel:
        """Parse a raw ground truth dict into a typed ``ground_truth_type`` instance."""
        gt_type = self.config.get_ground_truth_type()
        try:
            return gt_type.model_validate(raw)
        except Exception as exc:
            logger.warning(
                "ground_truth_type coercion failed (%s), wrapping raw dict",
                exc,
            )
            try:
                return gt_type.model_construct(**raw)
            except Exception:
                return gt_type()

    def _resolve_actuals(self, prediction) -> dict[str, Any] | None:
        """Resolve ground truth for a single prediction.

        - resolve_horizon_seconds=0 (resolvable_at == performed_at): immediate
          resolution with empty actuals (live trading).
        - Otherwise: fetch feed records in the horizon window and call
          resolve_ground_truth.
        """
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
                    "last record is %.1fs before horizon (max allowed: %.1fs = %.0f%% of horizon)",
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

    def _score_predictions(self, now: datetime) -> list[ScoreRecord]:
        predictions = self.prediction_repository.find(
            status=PredictionStatus.PENDING,
            resolvable_before=now,
        )
        if not predictions:
            return []

        scored: list[ScoreRecord] = []
        for prediction in predictions:
            actuals_dict = self._resolve_actuals(prediction)
            if actuals_dict is None:
                continue

            typed_output = self._coerce_output(prediction.inference_output)
            typed_gt = self._coerce_ground_truth(actuals_dict)

            if hasattr(typed_output, "model_config"):
                typed_output.__dict__["model_id"] = prediction.model_id
                typed_output.__dict__["prediction_id"] = prediction.id

            result = self.scoring_function(typed_output, typed_gt)
            result_dict = (
                result.model_dump() if isinstance(result, BaseModel) else result
            )
            validated = self.config.score_type.model_validate(result_dict)

            score = ScoreRecord(
                id=f"{SCORE_PREFIX}{prediction.id}",
                prediction_id=prediction.id,
                result=validated.model_dump(),
                success=True,
                scored_at=now,
            )

            model_name = getattr(prediction, "model_name", prediction.model_id)
            output_summary = (
                typed_output.model_dump()
                if isinstance(typed_output, BaseModel)
                else typed_output
            )
            gt_summary = (
                typed_gt.model_dump() if isinstance(typed_gt, BaseModel) else typed_gt
            )
            logger.info(
                "  scored model=%s prediction=%s output=%s gt=%s → score=%s",
                model_name,
                prediction.id,
                output_summary,
                gt_summary,
                validated.model_dump(),
            )

            if self.score_repository is not None:
                self.score_repository.save(score)

            prediction.status = PredictionStatus.SCORED
            self.prediction_repository.save(prediction)
            scored.append(score)

        if scored:
            logger.info("Scored %d predictions", len(scored))
        return scored

    def _write_snapshots(
        self, scored: list[ScoreRecord], now: datetime
    ) -> list[SnapshotRecord]:
        if self.snapshot_repository is None:
            return []

        pred_map: dict[str, str] = {}
        pred_by_id: dict[str, Any] = {}
        predictions = self.prediction_repository.find(status=PredictionStatus.SCORED)
        for p in predictions:
            pred_map[p.id] = p.model_id
            pred_by_id[p.id] = p

        by_model_scores: dict[str, list[dict[str, Any]]] = {}
        by_model_preds: dict[str, list[dict[str, Any]]] = {}
        by_model_score_dicts: dict[str, list[dict[str, Any]]] = {}

        for score in scored:
            model_id = pred_map.get(score.prediction_id)
            if not model_id:
                continue
            by_model_scores.setdefault(model_id, []).append(score.result)

            pred = pred_by_id.get(score.prediction_id)
            if pred:
                by_model_preds.setdefault(model_id, []).append(
                    {
                        "inference_output": pred.inference_output,
                        "performed_at": pred.performed_at,
                        "scope": pred.scope,
                    }
                )
            by_model_score_dicts.setdefault(model_id, []).append(
                {
                    "result": score.result,
                    "scored_at": score.scored_at,
                }
            )

        from crunch_node.metrics.context import MetricsContext

        metrics_context_base = MetricsContext(
            model_id="",
            window_start=min((s.scored_at for s in scored), default=now),
            window_end=now,
            all_model_predictions=by_model_preds,
        )

        written_snapshots: list[SnapshotRecord] = []

        for model_id, results in by_model_scores.items():
            summary = self.config.aggregate_snapshot(results)

            if self.config.metrics:
                ctx = MetricsContext(
                    model_id=model_id,
                    window_start=metrics_context_base.window_start,
                    window_end=metrics_context_base.window_end,
                    all_model_predictions=metrics_context_base.all_model_predictions,
                    ensemble_predictions=metrics_context_base.ensemble_predictions,
                )
                metric_results = self.config.compute_metrics(
                    self.config.metrics,
                    by_model_preds.get(model_id, []),
                    by_model_score_dicts.get(model_id, []),
                    ctx,
                )
                summary.update(metric_results)

            snapshot = SnapshotRecord(
                id=f"{SNAPSHOT_PREFIX}{model_id}_{now.strftime('%Y%m%d_%H%M%S')}",
                model_id=model_id,
                period_start=min(
                    s.scored_at
                    for s in scored
                    if pred_map.get(s.prediction_id) == model_id
                ),
                period_end=now,
                prediction_count=len(results),
                result_summary=summary,
                created_at=now,
            )
            self.snapshot_repository.save(snapshot)
            written_snapshots.append(snapshot)

            logger.info(
                "  snapshot model=%s predictions=%d summary=%s",
                model_id,
                len(results),
                summary,
            )

        logger.info("Wrote %d snapshots", len(by_model_scores))

        return written_snapshots
