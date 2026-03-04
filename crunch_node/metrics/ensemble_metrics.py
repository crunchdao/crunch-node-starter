"""Tier 3 ensemble-related metrics — computed when ensembling is enabled."""

from __future__ import annotations

from typing import Any

from crunch_node.metrics.builtins import (
    _extract_pred_values,
    _spearman_correlation,
)
from crunch_node.metrics.context import MetricsContext


def compute_ensemble_correlation(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """Correlation of this model's predictions to the (first) ensemble output."""
    my_vals = _extract_pred_values(predictions)
    if len(my_vals) < 2:
        return 0.0

    # Use the first ensemble's predictions
    for ens_name, ens_preds in context.ensemble_predictions.items():
        ens_vals = _extract_pred_values(ens_preds)
        if len(ens_vals) < 2:
            continue
        return _spearman_correlation(my_vals, ens_vals)

    return 0.0


def compute_contribution(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """Leave-one-out contribution — how much the ensemble score drops without this model.

    Positive = this model helps the ensemble. Negative = it hurts.

    Approximation: measures the correlation difference between the full ensemble
    and a "leave-one-out" ensemble (equal-weighted among remaining models).
    """
    my_vals = _extract_pred_values(predictions)
    if len(my_vals) < 2:
        return 0.0

    # Get the ensemble predictions and actual returns from scores
    ens_preds = None
    for _, ep in context.ensemble_predictions.items():
        ens_preds = ep
        break
    if not ens_preds:
        return 0.0

    ens_vals = _extract_pred_values(ens_preds)
    if len(ens_vals) < 2:
        return 0.0

    # Build leave-one-out ensemble: subtract this model's contribution
    other_models = {
        m: p
        for m, p in context.all_model_predictions.items()
        if m != context.model_id and not m.startswith("__ensemble_")
    }
    if not other_models:
        return 0.0

    # Simple equal-weight average of other models
    n_others = len(other_models)
    n_preds = min(len(my_vals), len(ens_vals))
    loo_vals = [0.0] * n_preds

    for _, other_preds in other_models.items():
        other_vals = _extract_pred_values(other_preds)
        for i in range(min(len(other_vals), n_preds)):
            loo_vals[i] += other_vals[i] / n_others

    # Extract actual returns for IC comparison
    from crunch_node.metrics.builtins import _extract_actual_returns

    actual_returns = _extract_actual_returns(scores)

    if len(actual_returns) < 2:
        return 0.0

    # IC of full ensemble vs IC of leave-one-out ensemble
    ic_full = _spearman_correlation(ens_vals[:n_preds], actual_returns[:n_preds])
    ic_loo = _spearman_correlation(loo_vals[:n_preds], actual_returns[:n_preds])

    return ic_full - ic_loo


def compute_fnc(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """Feature-Neutral Correlation — IC after orthogonalizing against other models.

    Simplified version: residual correlation after removing the mean prediction
    across all models.
    """
    my_vals = _extract_pred_values(predictions)
    if len(my_vals) < 2:
        return 0.0

    from crunch_node.metrics.builtins import _extract_actual_returns

    actual_returns = _extract_actual_returns(scores)
    n = min(len(my_vals), len(actual_returns))
    if n < 2:
        return 0.0

    # Compute mean prediction across all non-ensemble models
    other_models = {
        m: p
        for m, p in context.all_model_predictions.items()
        if not m.startswith("__ensemble_")
    }
    if len(other_models) <= 1:
        # Only this model — FNC = IC
        return _spearman_correlation(my_vals[:n], actual_returns[:n])

    n_models = len(other_models)
    mean_preds = [0.0] * n
    for _, preds in other_models.items():
        vals = _extract_pred_values(preds)
        for i in range(min(len(vals), n)):
            mean_preds[i] += vals[i] / n_models

    # Residual = my_vals - mean_preds
    residuals = [my_vals[i] - mean_preds[i] for i in range(n)]

    return _spearman_correlation(residuals, actual_returns[:n])
