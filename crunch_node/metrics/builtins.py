"""Built-in metric implementations for the metrics registry.

Each function follows the signature:
    fn(predictions: list[dict], scores: list[dict], context: MetricsContext) → float

Predictions carry `inference_output` (what the model predicted).
Scores carry `result` (the per-prediction scoring output, including ground truth info).
Context carries cross-model data for correlation/contribution metrics.
"""

from __future__ import annotations

import math
from typing import Any

from crunch_node.metrics.context import MetricsContext

# ── Helpers ──


def _extract_pred_values(predictions: list[dict[str, Any]]) -> list[float]:
    """Extract the prediction signal value from each prediction.

    Tries common field names in order: value, expected_return, signal, prediction.
    Falls back to the first numeric field found.
    """
    _SIGNAL_KEYS = ("value", "expected_return", "signal", "prediction")
    values = []
    for p in predictions:
        output = p.get("inference_output", {})
        v = None
        for key in _SIGNAL_KEYS:
            v = output.get(key)
            if v is not None:
                break
        if v is None:
            # Fallback: first numeric value in output
            for val in output.values():
                if isinstance(val, (int, float)):
                    v = val
                    break
        if v is not None:
            try:
                values.append(float(v))
            except (ValueError, TypeError):
                pass
    return values


def _extract_score_values(scores: list[dict[str, Any]]) -> list[float]:
    """Extract the primary score value from each score result."""
    values = []
    for s in scores:
        result = s.get("result", {})
        v = result.get("value")
        if v is not None:
            try:
                values.append(float(v))
            except (ValueError, TypeError):
                pass
    return values


def _extract_actual_returns(scores: list[dict[str, Any]]) -> list[float]:
    """Extract actual returns from score results (set by ground truth resolver)."""
    values = []
    for s in scores:
        result = s.get("result", {})
        # Try 'actual_return' first, then 'profit' from ground truth
        for key in ("actual_return", "profit"):
            v = result.get(key)
            if v is not None:
                try:
                    values.append(float(v))
                    break
                except (ValueError, TypeError):
                    pass
        else:
            values.append(0.0)
    return values


def _spearman_correlation(x: list[float], y: list[float]) -> float:
    """Compute Spearman rank correlation between two lists."""
    n = min(len(x), len(y))
    if n < 2:
        return 0.0

    def _rank(values: list[float]) -> list[float]:
        indexed = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        for rank, idx in enumerate(indexed):
            ranks[idx] = float(rank)
        return ranks

    rx = _rank(x[:n])
    ry = _rank(y[:n])

    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n

    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    std_x = math.sqrt(sum((rx[i] - mean_rx) ** 2 for i in range(n)))
    std_y = math.sqrt(sum((ry[i] - mean_ry) ** 2 for i in range(n)))

    if std_x < 1e-12 or std_y < 1e-12:
        return 0.0

    return cov / (std_x * std_y)


# ── Tier 1: Core metrics ──


def compute_ic(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """Information Coefficient — Spearman rank correlation between predictions and actual returns."""
    pred_vals = _extract_pred_values(predictions)
    actual_returns = _extract_actual_returns(scores)
    return _spearman_correlation(pred_vals, actual_returns)


def compute_ic_sharpe(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """IC Sharpe — mean(IC) / std(IC). Rewards consistency of IC over sub-windows.

    Splits the window into sub-periods and computes IC for each,
    then returns mean/std of those ICs.
    """
    pred_vals = _extract_pred_values(predictions)
    actual_returns = _extract_actual_returns(scores)
    n = min(len(pred_vals), len(actual_returns))
    if n < 4:
        return 0.0

    # Split into chunks of ~10 predictions each (min 3 chunks)
    chunk_size = max(2, n // max(3, n // 10))
    ics = []
    for start in range(0, n - chunk_size + 1, chunk_size):
        end = min(start + chunk_size, n)
        if end - start < 2:
            continue
        ic = _spearman_correlation(pred_vals[start:end], actual_returns[start:end])
        ics.append(ic)

    if len(ics) < 2:
        return 0.0

    mean_ic = sum(ics) / len(ics)
    std_ic = math.sqrt(sum((ic - mean_ic) ** 2 for ic in ics) / len(ics))

    if std_ic < 1e-12:
        # All chunk ICs identical — perfectly consistent signal.
        # Cap at 10.0 to avoid Infinity (not JSON-serializable for Postgres).
        return 10.0 if abs(mean_ic) > 1e-12 else 0.0

    return mean_ic / std_ic


def compute_mean_return(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """Mean return — average return of a long-short strategy from signals.

    Positive prediction → long, negative → short. Return = sign(pred) * actual_return.
    """
    pred_vals = _extract_pred_values(predictions)
    actual_returns = _extract_actual_returns(scores)
    n = min(len(pred_vals), len(actual_returns))
    if n == 0:
        return 0.0

    strategy_returns = []
    for i in range(n):
        sign = 1.0 if pred_vals[i] >= 0 else -1.0
        strategy_returns.append(sign * actual_returns[i])

    return sum(strategy_returns) / n


def compute_hit_rate(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """Hit rate — percentage of predictions with correct directional sign."""
    pred_vals = _extract_pred_values(predictions)
    actual_returns = _extract_actual_returns(scores)
    n = min(len(pred_vals), len(actual_returns))
    if n == 0:
        return 0.0

    correct = 0
    for i in range(n):
        pred_sign = 1 if pred_vals[i] >= 0 else -1
        actual_sign = 1 if actual_returns[i] >= 0 else -1
        if pred_sign == actual_sign:
            correct += 1

    return correct / n


def compute_model_correlation(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """Model correlation — mean pairwise Spearman correlation against all other models."""
    my_vals = _extract_pred_values(predictions)
    if len(my_vals) < 2:
        return 0.0

    correlations = []
    for other_id, other_preds in context.all_model_predictions.items():
        if other_id == context.model_id:
            continue
        if other_id.startswith("__ensemble_"):
            continue
        other_vals = _extract_pred_values(other_preds)
        if len(other_vals) < 2:
            continue
        corr = _spearman_correlation(my_vals, other_vals)
        correlations.append(corr)

    if not correlations:
        return 0.0

    return sum(correlations) / len(correlations)


# ── Tier 2: Risk/stability metrics ──


def compute_max_drawdown(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """Max drawdown — worst peak-to-trough on cumulative score values.

    Returns a negative number (or zero). More negative = worse drawdown.
    """
    score_vals = _extract_score_values(scores)
    if len(score_vals) < 2:
        return 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for val in score_vals:
        cumulative += val
        if cumulative > peak:
            peak = cumulative
        dd = cumulative - peak
        if dd < max_dd:
            max_dd = dd

    return max_dd


def compute_sortino_ratio(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """Sortino ratio — mean return / downside deviation.

    Like Sharpe but only penalizes negative returns.
    """
    pred_vals = _extract_pred_values(predictions)
    actual_returns = _extract_actual_returns(scores)
    n = min(len(pred_vals), len(actual_returns))
    if n < 2:
        return 0.0

    strategy_returns = []
    for i in range(n):
        sign = 1.0 if pred_vals[i] >= 0 else -1.0
        strategy_returns.append(sign * actual_returns[i])

    mean_ret = sum(strategy_returns) / n
    downside_sq = [r**2 for r in strategy_returns if r < 0]

    if not downside_sq:
        # No downside — cap at ±10.0 to keep values JSON-safe and displayable
        return min(10.0, max(-10.0, mean_ret * 1e6)) if mean_ret != 0 else 0.0

    downside_dev = math.sqrt(sum(downside_sq) / len(downside_sq))
    if downside_dev < 1e-12:
        return 0.0

    return mean_ret / downside_dev


def compute_turnover(
    predictions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    context: MetricsContext,
) -> float:
    """Turnover — mean absolute change in signal between consecutive predictions.

    Lower turnover = more stable signal. Returns average |pred[t] - pred[t-1]|.
    """
    pred_vals = _extract_pred_values(predictions)
    if len(pred_vals) < 2:
        return 0.0

    changes = [abs(pred_vals[i] - pred_vals[i - 1]) for i in range(1, len(pred_vals))]
    return sum(changes) / len(changes)
