"""Tests for MetricsRegistry."""

from __future__ import annotations

import unittest

from crunch_node.metrics.context import MetricsContext
from crunch_node.metrics.registry import MetricsRegistry, get_default_registry


class TestMetricsRegistry(unittest.TestCase):
    def test_register_and_compute(self):
        registry = MetricsRegistry()
        registry.register("dummy", lambda preds, scores, ctx: 42.0)

        result = registry.compute(
            metrics=["dummy"],
            predictions=[],
            scores=[],
            context=MetricsContext(model_id="m1"),
        )
        self.assertEqual(result, {"dummy": 42.0})

    def test_unknown_metric_skipped(self):
        registry = MetricsRegistry()
        result = registry.compute(
            metrics=["nonexistent"],
            predictions=[],
            scores=[],
            context=MetricsContext(model_id="m1"),
        )
        self.assertEqual(result, {})

    def test_failing_metric_returns_zero(self):
        def bad_metric(preds, scores, ctx):
            raise ValueError("boom")

        registry = MetricsRegistry()
        registry.register("bad", bad_metric)

        result = registry.compute(
            metrics=["bad"],
            predictions=[],
            scores=[],
            context=MetricsContext(model_id="m1"),
        )
        self.assertEqual(result, {"bad": 0.0})

    def test_multiple_metrics(self):
        registry = MetricsRegistry()
        registry.register("a", lambda p, s, c: 1.0)
        registry.register("b", lambda p, s, c: 2.0)
        registry.register("c", lambda p, s, c: 3.0)

        result = registry.compute(
            metrics=["a", "c"],
            predictions=[],
            scores=[],
            context=MetricsContext(model_id="m1"),
        )
        self.assertEqual(result, {"a": 1.0, "c": 3.0})

    def test_available_lists_registered(self):
        registry = MetricsRegistry()
        registry.register("b_metric", lambda p, s, c: 0)
        registry.register("a_metric", lambda p, s, c: 0)
        self.assertEqual(registry.available(), ["a_metric", "b_metric"])

    def test_get_returns_function(self):
        def fn(p, s, c):
            return 0

        registry = MetricsRegistry()
        registry.register("test", fn)
        self.assertIs(registry.get("test"), fn)
        self.assertIsNone(registry.get("missing"))

    def test_default_registry_has_builtins(self):
        registry = get_default_registry()
        expected = [
            "hit_rate",
            "ic",
            "ic_sharpe",
            "max_drawdown",
            "mean_return",
            "model_correlation",
            "sortino_ratio",
            "turnover",
        ]
        for name in expected:
            self.assertIn(
                name, registry.available(), f"{name} missing from default registry"
            )


if __name__ == "__main__":
    unittest.main()
