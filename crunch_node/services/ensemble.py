"""Ensemble service — combine multiple model predictions into virtual meta-models."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from crunch_node.crunch_config import EnsembleModelFilter
from crunch_node.entities.prediction import PredictionRecord, PredictionStatus

logger = logging.getLogger(__name__)

ENSEMBLE_PREFIX = "__ensemble_"
ENSEMBLE_SUFFIX = "__"


def ensemble_model_id(name: str) -> str:
    """Build the virtual model ID for a named ensemble."""
    return f"{ENSEMBLE_PREFIX}{name}{ENSEMBLE_SUFFIX}"


def is_ensemble_model(model_id: str) -> bool:
    """Check if a model ID belongs to an ensemble virtual model."""
    return model_id.startswith(ENSEMBLE_PREFIX)


# ── Built-in weight strategies ──


def inverse_variance(
    model_metrics: dict[str, dict[str, float]],
    predictions: dict[str, list[dict[str, Any]]],
) -> dict[str, float]:
    """Inverse-variance weighting — weight = 1/var(score_values), normalized.

    Uses the 'value' key from score results. Falls back to equal weight
    if variance cannot be computed for a model.
    """
    raw_weights: dict[str, float] = {}

    for model_id, preds in predictions.items():
        values = [
            float(p.get("inference_output", {}).get("value", 0))
            for p in preds
            if p.get("inference_output", {}).get("value") is not None
        ]
        if len(values) < 2:
            raw_weights[model_id] = 1.0
            continue

        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)

        if variance < 1e-12:
            raw_weights[model_id] = 1.0
        else:
            raw_weights[model_id] = 1.0 / variance

    total = sum(raw_weights.values())
    if total < 1e-12:
        n = len(raw_weights)
        return {m: 1.0 / n for m in raw_weights} if n > 0 else {}

    return {m: w / total for m, w in raw_weights.items()}


def equal_weight(
    model_metrics: dict[str, dict[str, float]],
    predictions: dict[str, list[dict[str, Any]]],
) -> dict[str, float]:
    """Equal weighting — 1/N for all models."""
    n = len(predictions)
    if n == 0:
        return {}
    return {m: 1.0 / n for m in predictions}


# ── Built-in model filters ──


def top_n(n: int) -> EnsembleModelFilter:
    """Factory for a filter that keeps the top N models by ranking metric.

    Usage: model_filter=top_n(5)
    Note: The filter function is stateful — it must be used with a metrics dict
    that contains a 'value' key (the primary score).
    """

    def _filter(model_id: str, metrics: dict[str, float]) -> bool:
        # The actual filtering is done in apply_model_filter which sorts all models
        return True  # placeholder — real logic in apply_model_filter

    _filter._top_n = n  # type: ignore[attr-defined]
    return _filter


def min_metric(name: str, threshold: float) -> EnsembleModelFilter:
    """Factory for a filter that keeps models above a metric threshold."""

    def _filter(model_id: str, metrics: dict[str, float]) -> bool:
        return metrics.get(name, 0.0) >= threshold

    return _filter


def apply_model_filter(
    model_filter: EnsembleModelFilter | Callable | None,
    model_metrics: dict[str, dict[str, float]],
    predictions: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Apply a model filter to select which models participate in the ensemble."""
    if model_filter is None:
        return predictions

    # Handle top_n special case
    if hasattr(model_filter, "_top_n"):
        n = model_filter._top_n
        # Sort models by primary 'value' metric descending
        ranked = sorted(
            predictions.keys(),
            key=lambda m: model_metrics.get(m, {}).get("value", 0.0),
            reverse=True,
        )
        kept = set(ranked[:n])
        return {m: p for m, p in predictions.items() if m in kept}

    # Standard filter: call per model
    return {
        m: p
        for m, p in predictions.items()
        if model_filter(m, model_metrics.get(m, {}))
    }


# ── Ensemble prediction builder ──


def build_ensemble_predictions(
    name: str,
    weights: dict[str, float],
    predictions_by_model: dict[str, list[dict[str, Any]]],
    now: datetime | None = None,
) -> list[PredictionRecord]:
    """Build weighted-average ensemble PredictionRecords from model predictions.

    Groups predictions by (input_id, scope_key), computes weighted average of
    inference_output['value'], produces one PredictionRecord per group.
    """
    if now is None:
        now = datetime.now(UTC)

    virtual_model_id = ensemble_model_id(name)

    # Group predictions by (input_id, scope_key) across all models
    groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for model_id, preds in predictions_by_model.items():
        if model_id not in weights:
            continue
        for p in preds:
            key = (p.get("input_id", ""), p.get("scope_key", ""))
            groups.setdefault(key, {})[model_id] = p

    ensemble_preds = []
    for (input_id, scope_key), model_preds in groups.items():
        weighted_sum = 0.0
        weight_sum = 0.0

        for model_id, pred in model_preds.items():
            w = weights.get(model_id, 0.0)
            val = pred.get("inference_output", {}).get("value")
            if val is not None:
                try:
                    weighted_sum += w * float(val)
                    weight_sum += w
                except (ValueError, TypeError):
                    pass

        if weight_sum < 1e-12:
            continue

        ensemble_value = weighted_sum / weight_sum

        ensemble_preds.append(
            PredictionRecord(
                id=f"pred_{virtual_model_id}_{input_id}_{scope_key}",
                input_id=input_id,
                model_id=virtual_model_id,
                prediction_config_id=None,
                scope_key=scope_key,
                scope=next(iter(model_preds.values()), {}).get("scope", {}),
                status=PredictionStatus.SCORED,
                exec_time_ms=0.0,
                inference_output={"value": ensemble_value},
                meta={"weights": weights, "ensemble_name": name},
                performed_at=now,
                resolvable_at=now,
            )
        )

    return ensemble_preds
