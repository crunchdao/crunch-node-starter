"""Pluggable metrics registry — compute named metrics from scored predictions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from crunch_node.metrics.context import MetricsContext

logger = logging.getLogger(__name__)

# Metric function signature:
#   fn(predictions: list[dict], scores: list[dict], context: MetricsContext) → float
MetricFn = Callable[[list[dict[str, Any]], list[dict[str, Any]], MetricsContext], float]


class MetricsRegistry:
    """Registry of named metric functions.

    Usage:
        registry = MetricsRegistry()
        registry.register("ic", compute_ic)

        results = registry.compute(
            metrics=["ic", "ic_sharpe"],
            predictions=preds,
            scores=scores,
            context=ctx,
        )
        # → {"ic": 0.035, "ic_sharpe": 1.2}
    """

    def __init__(self) -> None:
        self._metrics: dict[str, MetricFn] = {}

    def register(self, name: str, fn: MetricFn) -> None:
        """Register a metric function by name."""
        self._metrics[name] = fn

    def get(self, name: str) -> MetricFn | None:
        """Get a metric function by name."""
        return self._metrics.get(name)

    def available(self) -> list[str]:
        """List all registered metric names."""
        return sorted(self._metrics.keys())

    def compute(
        self,
        metrics: list[str],
        predictions: list[dict[str, Any]],
        scores: list[dict[str, Any]],
        context: MetricsContext,
    ) -> dict[str, float]:
        """Compute all requested metrics, returning name → value.

        Skips metrics that aren't registered or that raise exceptions.
        """
        results: dict[str, float] = {}
        for name in metrics:
            fn = self._metrics.get(name)
            if fn is None:
                logger.warning("metric %r not registered, skipping", name)
                continue
            try:
                results[name] = fn(predictions, scores, context)
            except Exception as exc:
                logger.warning("metric %r failed: %s", name, exc)
                results[name] = 0.0
        return results


# ── Global default registry ──

_default_registry: MetricsRegistry | None = None


def get_default_registry() -> MetricsRegistry:
    """Get the global default registry, creating and populating it on first call."""
    global _default_registry
    if _default_registry is None:
        _default_registry = MetricsRegistry()
        _register_builtins(_default_registry)
    return _default_registry


def _register_builtins(registry: MetricsRegistry) -> None:
    """Register all built-in metrics."""
    from crunch_node.metrics.builtins import (
        compute_hit_rate,
        compute_ic,
        compute_ic_sharpe,
        compute_max_drawdown,
        compute_mean_return,
        compute_model_correlation,
        compute_sortino_ratio,
        compute_turnover,
    )
    from crunch_node.metrics.ensemble_metrics import (
        compute_contribution,
        compute_ensemble_correlation,
        compute_fnc,
    )

    # Tier 1
    registry.register("ic", compute_ic)
    registry.register("ic_sharpe", compute_ic_sharpe)
    registry.register("mean_return", compute_mean_return)
    registry.register("hit_rate", compute_hit_rate)
    registry.register("model_correlation", compute_model_correlation)

    # Tier 2
    registry.register("max_drawdown", compute_max_drawdown)
    registry.register("sortino_ratio", compute_sortino_ratio)
    registry.register("turnover", compute_turnover)

    # Tier 3 (ensemble-aware)
    registry.register("fnc", compute_fnc)
    registry.register("contribution", compute_contribution)
    registry.register("ensemble_correlation", compute_ensemble_correlation)
