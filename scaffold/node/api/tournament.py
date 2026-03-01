"""Tournament API — round-based inference and scoring endpoints.

Auto-discovered by the report worker. Provides two endpoints:

    POST /tournament/rounds/{round_id}/inference  — upload features, run models
    POST /tournament/rounds/{round_id}/score      — upload ground truth, score round
    GET  /tournament/rounds/{round_id}/status      — check round state
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tournament", tags=["tournament"])

# ── Service singleton (lazy-initialized) ──

_service = None


def _get_service():
    """Lazy-load the tournament predict service.

    The service is built once and cached. Uses the same config loading
    as the predict worker.
    """
    global _service
    if _service is not None:
        return _service

    from coordinator_node.config.extensions import ExtensionSettings
    from coordinator_node.config_loader import load_config
    from coordinator_node.db import (
        DBInputRepository,
        DBModelRepository,
        DBPredictionRepository,
        DBScoreRepository,
        create_session,
    )
    from coordinator_node.extensions.callable_resolver import resolve_callable
    from coordinator_node.services.tournament_predict import TournamentPredictService

    config = load_config()
    session = create_session()

    # Resolve scoring function (same as score_worker)
    scoring_function = config.scoring_function
    if scoring_function is None:
        try:
            extension_settings = ExtensionSettings.from_env()
            scoring_function = resolve_callable(
                extension_settings.scoring_function,
                required_params=("prediction", "ground_truth"),
            )
        except Exception as exc:
            logger.warning("Could not resolve scoring function: %s", exc)

    _service = TournamentPredictService(
        contract=config,
        input_repository=DBInputRepository(session),
        model_repository=DBModelRepository(session),
        prediction_repository=DBPredictionRepository(session),
        score_repository=DBScoreRepository(session),
        scoring_function=scoring_function,
    )

    return _service


# ── Request/Response models ──


class InferenceRequest(BaseModel):
    """Request body for running inference on a batch of features."""

    features: list[dict[str, Any]] = Field(
        ..., description="List of feature dicts to run models on"
    )


class InferenceResponse(BaseModel):
    """Response from inference endpoint."""

    round_id: str
    model_count: int
    prediction_count: int
    status: str


class ScoreRequest(BaseModel):
    """Request body for scoring a round with ground truth."""

    ground_truth: dict[str, Any] | list[dict[str, Any]] = Field(
        ..., description="Ground truth data (single dict or list of dicts)"
    )


class ScoreResponse(BaseModel):
    """Response from scoring endpoint."""

    round_id: str
    scores_count: int
    results: list[dict[str, Any]]


class RoundStatusResponse(BaseModel):
    """Response from round status endpoint."""

    round_id: str
    status: str
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)


# ── Endpoints ──


@router.post(
    "/rounds/{round_id}/inference",
    response_model=InferenceResponse,
)
async def run_inference(round_id: str, request: InferenceRequest):
    """Run all registered models on the provided features batch.

    Creates PredictionRecords for each model, linked to the round via scope_key.
    """
    service = _get_service()

    if not request.features:
        raise HTTPException(status_code=400, detail="features list is empty")

    try:
        predictions = await service.run_inference(round_id, request.features)
    except Exception as exc:
        logger.exception("Inference failed for round %s", round_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    model_ids = {p.model_id for p in predictions}
    return InferenceResponse(
        round_id=round_id,
        model_count=len(model_ids),
        prediction_count=len(predictions),
        status="inference_complete",
    )


@router.post(
    "/rounds/{round_id}/score",
    response_model=ScoreResponse,
)
async def score_round(round_id: str, request: ScoreRequest):
    """Score all pending predictions for a round against ground truth.

    The scoring function is called for each model's prediction.
    Creates ScoreRecords that the score service picks up for
    snapshots and leaderboard updates.
    """
    service = _get_service()

    try:
        scores = service.score_round(round_id, request.ground_truth)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Scoring failed for round %s", round_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ScoreResponse(
        round_id=round_id,
        scores_count=len(scores),
        results=[
            {
                "prediction_id": s.prediction_id,
                "score": s.result.get("value", 0.0),
                "success": s.success,
                "result": s.result,
            }
            for s in scores
        ],
    )


@router.get(
    "/rounds/{round_id}/status",
    response_model=RoundStatusResponse,
)
async def round_status(round_id: str):
    """Get the current status of a tournament round."""
    service = _get_service()
    status = service.get_round_status(round_id)
    return RoundStatusResponse(**status)
