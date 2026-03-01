"""Tournament predict service: round-based, request-driven batch inference and scoring.

Unlike RealtimePredictService (continuous event loop), this service is triggered
by explicit API calls:

1. ``run_inference(round_id, features)`` — runs all models on a batch of features
2. ``score_round(round_id, ground_truth)`` — scores predictions against ground truth

There is no feed, no polling, no ticking. Rounds are explicit scope_key strings.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from coordinator_node.crunch_config import CrunchConfig
from coordinator_node.db.repositories import (
    DBInputRepository,
    DBModelRepository,
    DBPredictionRepository,
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
        """Run all models on a batch of features for a tournament round.

        Args:
            round_id: Unique round identifier (becomes scope_key).
            features: List of feature dicts (validated as input_type).
            now: Override timestamp (for testing).

        Returns:
            List of PredictionRecords (one per model).
        """
        now = now or datetime.now(UTC)
        await self.init_runner()

        # Validate features through input_type
        validated_features = [
            self.contract.input_type.model_validate(f).model_dump() for f in features
        ]

        # Save input record (features only, no ground truth)
        inp = InputRecord(
            id=f"INP_{round_id}_{now.strftime('%Y%m%d_%H%M%S')}",
            raw_data={"round_id": round_id, "features": validated_features},
            received_at=now,
        )
        if self.input_repository is not None:
            self.input_repository.save(inp)

        # Build scope for model calls
        scope = {
            "scope_key": round_id,
            "round_id": round_id,
            "features": validated_features,
        }

        # Call all models with tournament-specific encoding (features as JSON)
        responses = await self._call_models_tournament(scope)
        predictions: list[PredictionRecord] = []
        seen: set[str] = set()

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
                output = {"_validation_error": validation_error, "raw_output": output}
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
                    scope_key=round_id,
                    scope=scope,
                    status=status,
                    output=output,
                    now=now,
                    resolvable_at=None,  # No horizon — scored explicitly
                    exec_time_ms=float(getattr(result, "exec_time_us", 0.0)),
                    config_id=None,
                )
            )

        # Mark absent models
        for model_id in self._known_models:
            if model_id not in seen:
                predictions.append(
                    self._build_record(
                        model_id=model_id,
                        input_id=inp.id,
                        scope_key=round_id,
                        scope=scope,
                        status=PredictionStatus.ABSENT,
                        output={},
                        now=now,
                        resolvable_at=None,
                        config_id=None,
                    )
                )

        self._save(predictions)

        logger.info(
            "Round %s inference complete: %d models, %d predictions",
            round_id,
            len(seen),
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

        Args:
            round_id: Round identifier (scope_key used in run_inference).
            ground_truth: Ground truth data (validated as ground_truth_type).
                Can be a single dict or list of dicts depending on competition.
            now: Override timestamp (for testing).

        Returns:
            List of ScoreRecords.
        """
        now = now or datetime.now(UTC)

        if self._scoring_function is None:
            raise RuntimeError(
                "No scoring_function configured. Set scoring_function in "
                "CrunchConfig or pass it to TournamentPredictService."
            )

        # Validate ground truth
        if isinstance(ground_truth, list):
            validated_gt = [
                self.contract.ground_truth_type.model_validate(gt).model_dump()
                for gt in ground_truth
            ]
            gt_for_scoring = {"items": validated_gt}
        else:
            validated_gt = self.contract.ground_truth_type.model_validate(
                ground_truth
            ).model_dump()
            gt_for_scoring = validated_gt

        # Find pending predictions for this round
        predictions = self.prediction_repository.find(
            scope_key=round_id,
            status=PredictionStatus.PENDING,
        )

        if not predictions:
            logger.warning("No pending predictions found for round %s", round_id)
            return []

        scored: list[ScoreRecord] = []

        for prediction in predictions:
            typed_output = prediction.inference_output or {}

            # Inject prediction metadata for scoring function
            typed_output["model_id"] = prediction.model_id
            typed_output["prediction_id"] = prediction.id

            try:
                result = self._scoring_function(typed_output, gt_for_scoring)
                validated_result = self.contract.score_type.model_validate(result)

                score = ScoreRecord(
                    id=f"SCR_{prediction.id}",
                    prediction_id=prediction.id,
                    result=validated_result.model_dump(),
                    success=True,
                    scored_at=now,
                )
            except Exception as exc:
                logger.error("Scoring failed for prediction %s: %s", prediction.id, exc)
                score = ScoreRecord(
                    id=f"SCR_{prediction.id}",
                    prediction_id=prediction.id,
                    result={"value": 0.0, "success": False, "failed_reason": str(exc)},
                    success=False,
                    failed_reason=str(exc),
                    scored_at=now,
                )

            if self.score_repository is not None:
                self.score_repository.save(score)

            prediction.status = PredictionStatus.SCORED
            self.prediction_repository.save(prediction)
            scored.append(score)

        logger.info(
            "Round %s scoring complete: %d predictions scored", round_id, len(scored)
        )
        return scored

    # ── model calling ──

    async def _call_models_tournament(self, scope: dict[str, Any]) -> dict:
        """Call models with tournament data (features as JSON).

        Bypasses the realtime ``_encode_predict`` which expects scope args
        like subject/horizon. Tournament models receive the full scope
        (including features batch) as a single JSON argument.
        """
        method = self.contract.call_method.method
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
                            value=encode_data(VariantType.JSON, scope),
                        ),
                    )
                ],
                [],
            )
        except ImportError:
            # No model-runner-client — pass scope directly (for testing)
            args = (scope,)

        return await self._runner.call(method, args)

    # ── round queries ──

    def get_round_predictions(
        self, round_id: str, status: str | None = None
    ) -> list[PredictionRecord]:
        """Query predictions for a round, optionally filtered by status."""
        kwargs: dict[str, Any] = {"scope_key": round_id}
        if status is not None:
            kwargs["status"] = status
        return self.prediction_repository.find(**kwargs)

    def get_round_status(self, round_id: str) -> dict[str, Any]:
        """Get a summary of a round's state."""
        all_preds = self.prediction_repository.find(scope_key=round_id)
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

        return {
            "round_id": round_id,
            "status": status,
            "total": len(all_preds),
            "by_status": by_status,
        }
