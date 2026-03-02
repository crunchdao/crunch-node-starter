"""Tournament predict service: round-based, request-driven inference and scoring.

Unlike RealtimePredictService (continuous event loop), this service is triggered
by explicit API calls:

1. ``run_inference(round_id, features)`` — runs all models on each feature sample
2. ``score_round(round_id, ground_truth)`` — scores predictions against ground truth

There is no feed, no polling, no ticking. Rounds are explicit scope_key strings.

**Per-sample calling**: each model is called once per feature (not once per batch).
This produces N × M predictions (N samples × M models), each scored 1:1 against
the corresponding ground truth item.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from coordinator_node.db.repositories import (
    DBScoreRepository,
)
from coordinator_node.entities.prediction import (
    InputRecord,
    PredictionRecord,
    PredictionStatus,
    ScoreRecord,
)
from coordinator_node.services.predict import PredictService

logger = logging.getLogger(__name__)


class TournamentPredictService(PredictService):
    """Round-based tournament service.

    Not a loop — work is triggered by ``run_inference()`` and ``score_round()``.
    The ``run()`` method simply waits for shutdown (keeps the worker alive
    for the model runner sync task).

    Usage from API endpoints::

        # 1. Upload features → run inference
        predictions = await service.run_inference("round-001", features_data)

        # 2. Later, upload ground truth → score
        scores = service.score_round("round-001", ground_truth_data)
    """

    def __init__(
        self,
        score_repository: DBScoreRepository | None = None,
        scoring_function: Any | None = None,
        **kwargs: Any,
    ) -> None:
        # Tournament doesn't use a feed_reader, but PredictService requires one.
        # Pass None-safe — subclass methods never call feed_reader.
        if "feed_reader" not in kwargs:
            kwargs["feed_reader"] = None
        super().__init__(**kwargs)
        self.score_repository = score_repository
        self._scoring_function = scoring_function

    # ── main loop (no-op, just stays alive) ──

    async def run(self) -> None:
        """Keep the worker alive for model runner sync. No prediction loop."""
        logger.info("tournament predict service started (waiting for API calls)")
        await self.init_runner()
        await self.stop_event.wait()

    # ── round: inference ──

    async def run_inference(
        self,
        round_id: str,
        features: list[dict[str, Any]],
        now: datetime | None = None,
    ) -> list[PredictionRecord]:
        """Run all models on each feature sample for a tournament round.

        Each model is called once per feature, producing one prediction per
        (model, sample) pair. This means N features × M models = N×M
        predictions, each independently scoreable.

        Args:
            round_id: Unique round identifier.
            features: List of feature dicts (validated as input_type).
            now: Override timestamp (for testing).

        Returns:
            List of PredictionRecords (N × M).
        """
        now = now or datetime.now(UTC)
        await self.init_runner()

        # Validate features through input_type
        validated_features = [
            self.contract.input_type.model_validate(f).model_dump() for f in features
        ]

        # Save input record (features batch)
        inp = InputRecord(
            id=f"INP_{round_id}_{now.strftime('%Y%m%d_%H%M%S')}",
            raw_data={"round_id": round_id, "features": validated_features},
            received_at=now,
        )
        if self.input_repository is not None:
            self.input_repository.save(inp)

        predictions: list[PredictionRecord] = []
        seen: set[str] = set()

        for idx, feature in enumerate(validated_features):
            # Per-sample scope key for unique prediction IDs
            sample_key = f"{round_id}:{idx}"
            scope = {
                "scope_key": sample_key,
                "round_id": round_id,
                "feature_index": idx,
                "features": feature,
            }

            # Call all models with this single feature
            responses = await self._call_models_tournament(scope)

            # Offset timestamp to guarantee unique prediction IDs
            sample_now = now + timedelta(microseconds=idx)

            for model_run, result in responses.items():
                model = self._to_model(model_run)
                self.register_model(model)
                seen.add(model.id)

                raw_status = getattr(result, "status", "UNKNOWN")
                runner_status = (
                    str(raw_status.value)
                    if hasattr(raw_status, "value")
                    else str(raw_status)
                )

                output = getattr(result, "result", {})
                output = output if isinstance(output, dict) else {"result": output}

                validation_error = self.validate_output(output)
                if validation_error:
                    status = PredictionStatus.FAILED
                    output = {
                        "_validation_error": validation_error,
                        "raw_output": output,
                    }
                elif runner_status == "SUCCESS":
                    status = PredictionStatus.PENDING
                else:
                    status = (
                        PredictionStatus(runner_status)
                        if runner_status in PredictionStatus.__members__
                        else PredictionStatus.FAILED
                    )

                predictions.append(
                    self._build_record(
                        model_id=model.id,
                        input_id=inp.id,
                        scope_key=sample_key,
                        scope=scope,
                        status=status,
                        output=output,
                        now=sample_now,
                        resolvable_at=None,
                        exec_time_ms=float(getattr(result, "exec_time_us", 0.0)),
                        config_id=None,
                    )
                )

        # Mark absent models for each sample
        for idx in range(len(validated_features)):
            sample_key = f"{round_id}:{idx}"
            scope = {
                "scope_key": sample_key,
                "round_id": round_id,
                "feature_index": idx,
            }
            sample_now = now + timedelta(microseconds=idx)
            for model_id in self._known_models:
                if model_id not in seen:
                    predictions.append(
                        self._build_record(
                            model_id=model_id,
                            input_id=inp.id,
                            scope_key=sample_key,
                            scope=scope,
                            status=PredictionStatus.ABSENT,
                            output={},
                            now=sample_now,
                            resolvable_at=None,
                            config_id=None,
                        )
                    )

        self._save(predictions)

        logger.info(
            "Round %s inference complete: %d models, %d samples, %d predictions",
            round_id,
            len(seen),
            len(validated_features),
            len(predictions),
        )
        return predictions

    # ── round: scoring ──

    def score_round(
        self,
        round_id: str,
        ground_truth: dict[str, Any] | list[dict[str, Any]],
        now: datetime | None = None,
    ) -> list[ScoreRecord]:
        """Score all predictions for a round against ground truth.

        Predictions are grouped by model. Each model's per-sample predictions
        are sorted by feature_index and scored 1:1 against the corresponding
        ground truth item. This produces one ScoreRecord per prediction.

        Args:
            round_id: Round identifier used in run_inference.
            ground_truth: Ground truth data. List of dicts (one per sample)
                or a single dict for single-sample rounds.
            now: Override timestamp (for testing).

        Returns:
            List of ScoreRecords (one per prediction).
        """
        now = now or datetime.now(UTC)

        if self._scoring_function is None:
            raise RuntimeError(
                "No scoring_function configured. Set scoring_function in "
                "CrunchConfig or pass it to TournamentPredictService."
            )

        # Normalize ground truth to list
        if isinstance(ground_truth, list):
            gt_items = [
                self.contract.ground_truth_type.model_validate(gt).model_dump()
                for gt in ground_truth
            ]
        else:
            gt_items = [
                self.contract.ground_truth_type.model_validate(
                    ground_truth
                ).model_dump()
            ]

        # Find all pending predictions for this round (prefix match)
        predictions = self.prediction_repository.find(
            scope_key_prefix=f"{round_id}:",
            status=PredictionStatus.PENDING,
        )

        # Fallback: try exact match (single-sample rounds use scope_key=round_id)
        if not predictions:
            predictions = self.prediction_repository.find(
                scope_key=round_id,
                status=PredictionStatus.PENDING,
            )

        if not predictions:
            logger.warning("No pending predictions found for round %s", round_id)
            return []

        # Group by model, sort by feature_index
        by_model: dict[str, list[PredictionRecord]] = defaultdict(list)
        for p in predictions:
            by_model[p.model_id].append(p)

        scored: list[ScoreRecord] = []

        for model_id, model_preds in by_model.items():
            # Sort by feature_index from scope
            model_preds.sort(key=lambda p: (p.scope or {}).get("feature_index", 0))

            # Score each prediction against corresponding GT item
            if len(model_preds) != len(gt_items):
                logger.warning(
                    "Round %s model %s: %d predictions vs %d GT items — scoring min(%d, %d)",
                    round_id,
                    model_id,
                    len(model_preds),
                    len(gt_items),
                    len(model_preds),
                    len(gt_items),
                )
            for pred, gt in zip(model_preds, gt_items):
                typed_output = dict(pred.inference_output or {})
                typed_output["model_id"] = pred.model_id
                typed_output["prediction_id"] = pred.id

                try:
                    result = self._scoring_function(typed_output, gt)
                    validated_result = self.contract.score_type.model_validate(result)

                    score = ScoreRecord(
                        id=f"SCR_{pred.id}",
                        prediction_id=pred.id,
                        result=validated_result.model_dump(),
                        success=True,
                        scored_at=now,
                    )
                except Exception as exc:
                    logger.error("Scoring failed for prediction %s: %s", pred.id, exc)
                    score = ScoreRecord(
                        id=f"SCR_{pred.id}",
                        prediction_id=pred.id,
                        result={
                            "value": 0.0,
                            "success": False,
                            "failed_reason": str(exc),
                        },
                        success=False,
                        failed_reason=str(exc),
                        scored_at=now,
                    )

                if self.score_repository is not None:
                    self.score_repository.save(score)

                pred.status = PredictionStatus.SCORED
                self.prediction_repository.save(pred)
                scored.append(score)

        logger.info(
            "Round %s scoring complete: %d models, %d predictions scored",
            round_id,
            len(by_model),
            len(scored),
        )
        return scored

    # ── model calling ──

    async def _call_models_tournament(self, scope: dict[str, Any]) -> dict:
        """Call models with a single feature sample as JSON.

        Bypasses the realtime ``_encode_predict`` which expects scope args
        like subject/horizon. Tournament models receive one feature dict
        as a single JSON argument.
        """
        method = self.contract.call_method.method
        features = scope.get("features", scope)
        try:
            from model_runner_client.grpc.generated.commons_pb2 import (
                Argument,
                Variant,
                VariantType,
            )
            from model_runner_client.utils.datatype_transformer import encode_data

            args = (
                [
                    Argument(
                        position=1,
                        data=Variant(
                            type=VariantType.JSON,
                            value=encode_data(VariantType.JSON, features),
                        ),
                    )
                ],
                [],
            )
        except ImportError:
            # No model-runner-client — pass features directly (for testing)
            args = (features,)

        return await self._runner.call(method, args)

    # ── round queries ──

    def get_round_predictions(
        self, round_id: str, status: str | None = None
    ) -> list[PredictionRecord]:
        """Query predictions for a round, optionally filtered by status."""
        kwargs: dict[str, Any] = {"scope_key_prefix": f"{round_id}:"}
        if status is not None:
            kwargs["status"] = status
        preds = self.prediction_repository.find(**kwargs)
        if not preds:
            # Fallback: single-sample round with exact scope_key
            kwargs["scope_key"] = round_id
            del kwargs["scope_key_prefix"]
            preds = self.prediction_repository.find(**kwargs)
        return preds

    def get_round_status(self, round_id: str) -> dict[str, Any]:
        """Get a summary of a round's state."""
        all_preds = self.get_round_predictions(round_id)
        if not all_preds:
            return {"round_id": round_id, "status": "not_found", "total": 0}

        by_status: dict[str, int] = {}
        for p in all_preds:
            by_status[p.status] = by_status.get(p.status, 0) + 1

        if by_status.get(PredictionStatus.SCORED, 0) == len(all_preds):
            status = "scored"
        elif by_status.get(PredictionStatus.PENDING, 0) > 0:
            status = "inference_complete"
        else:
            status = "partial"

        # Count unique models
        model_ids = {p.model_id for p in all_preds}

        return {
            "round_id": round_id,
            "status": status,
            "total": len(all_preds),
            "model_count": len(model_ids),
            "by_status": by_status,
        }
