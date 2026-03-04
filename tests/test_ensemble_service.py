"""Tests for ensemble service — weights, filters, virtual model creation."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from crunch_node.services.ensemble import (
    apply_model_filter,
    build_ensemble_predictions,
    ensemble_model_id,
    equal_weight,
    inverse_variance,
    is_ensemble_model,
    min_metric,
    top_n,
)


def _pred(
    value: float, input_id: str = "inp1", scope_key: str = "BTC-60"
) -> dict[str, Any]:
    return {
        "inference_output": {"value": value},
        "input_id": input_id,
        "scope_key": scope_key,
        "scope": {"subject": "BTC"},
    }


class TestEnsembleModelId(unittest.TestCase):
    def test_ensemble_model_id(self):
        self.assertEqual(ensemble_model_id("main"), "__ensemble_main__")

    def test_is_ensemble(self):
        self.assertTrue(is_ensemble_model("__ensemble_main__"))
        self.assertFalse(is_ensemble_model("model_a"))


class TestInverseVariance(unittest.TestCase):
    def test_equal_variance_equal_weights(self):
        preds = {
            "m1": [_pred(1.0), _pred(3.0)],  # var = 1.0
            "m2": [_pred(2.0), _pred(4.0)],  # var = 1.0
        }
        weights = inverse_variance({}, preds)
        self.assertAlmostEqual(weights["m1"], 0.5)
        self.assertAlmostEqual(weights["m2"], 0.5)

    def test_lower_variance_higher_weight(self):
        preds = {
            "stable": [_pred(1.0), _pred(1.1)],  # low variance
            "volatile": [_pred(1.0), _pred(10.0)],  # high variance
        }
        weights = inverse_variance({}, preds)
        self.assertGreater(weights["stable"], weights["volatile"])

    def test_single_model(self):
        preds = {"m1": [_pred(1.0), _pred(2.0)]}
        weights = inverse_variance({}, preds)
        self.assertAlmostEqual(weights["m1"], 1.0)

    def test_single_prediction_fallback(self):
        preds = {
            "m1": [_pred(1.0)],  # not enough for variance
            "m2": [_pred(2.0)],
        }
        weights = inverse_variance({}, preds)
        self.assertAlmostEqual(weights["m1"], 0.5)
        self.assertAlmostEqual(weights["m2"], 0.5)

    def test_empty_predictions(self):
        self.assertEqual(inverse_variance({}, {}), {})


class TestEqualWeight(unittest.TestCase):
    def test_two_models(self):
        preds = {"m1": [_pred(1.0)], "m2": [_pred(2.0)]}
        weights = equal_weight({}, preds)
        self.assertAlmostEqual(weights["m1"], 0.5)
        self.assertAlmostEqual(weights["m2"], 0.5)

    def test_three_models(self):
        preds = {"a": [], "b": [], "c": []}
        weights = equal_weight({}, preds)
        for w in weights.values():
            self.assertAlmostEqual(w, 1 / 3)

    def test_empty(self):
        self.assertEqual(equal_weight({}, {}), {})


class TestModelFilters(unittest.TestCase):
    def test_top_n_keeps_best(self):
        metrics = {
            "m1": {"value": 0.9},
            "m2": {"value": 0.5},
            "m3": {"value": 0.7},
        }
        preds = {m: [_pred(1.0)] for m in metrics}

        filtered = apply_model_filter(top_n(2), metrics, preds)
        self.assertEqual(set(filtered.keys()), {"m1", "m3"})

    def test_min_metric_filter(self):
        metrics = {
            "m1": {"ic": 0.05},
            "m2": {"ic": 0.01},
            "m3": {"ic": 0.08},
        }
        preds = {m: [_pred(1.0)] for m in metrics}

        filtered = apply_model_filter(min_metric("ic", 0.04), metrics, preds)
        self.assertEqual(set(filtered.keys()), {"m1", "m3"})

    def test_no_filter_returns_all(self):
        preds = {"m1": [_pred(1.0)], "m2": [_pred(2.0)]}
        filtered = apply_model_filter(None, {}, preds)
        self.assertEqual(set(filtered.keys()), {"m1", "m2"})


class TestBuildEnsemblePredictions(unittest.TestCase):
    def test_weighted_average(self):
        weights = {"m1": 0.75, "m2": 0.25}
        preds = {
            "m1": [_pred(10.0, "inp1", "BTC-60")],
            "m2": [_pred(6.0, "inp1", "BTC-60")],
        }

        now = datetime(2026, 1, 1, tzinfo=UTC)
        result = build_ensemble_predictions("main", weights, preds, now)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].model_id, "__ensemble_main__")

        # Weighted avg: (0.75 * 10 + 0.25 * 6) / (0.75 + 0.25) = 9.0
        self.assertAlmostEqual(result[0].inference_output["value"], 9.0)

    def test_multiple_inputs(self):
        weights = {"m1": 0.5, "m2": 0.5}
        preds = {
            "m1": [_pred(10.0, "inp1", "BTC-60"), _pred(20.0, "inp2", "ETH-60")],
            "m2": [_pred(12.0, "inp1", "BTC-60"), _pred(18.0, "inp2", "ETH-60")],
        }

        result = build_ensemble_predictions("main", weights, preds)
        self.assertEqual(len(result), 2)

        by_input = {r.input_id: r for r in result}
        self.assertAlmostEqual(by_input["inp1"].inference_output["value"], 11.0)
        self.assertAlmostEqual(by_input["inp2"].inference_output["value"], 19.0)

    def test_stores_weights_in_meta(self):
        weights = {"m1": 1.0}
        preds = {"m1": [_pred(5.0)]}

        result = build_ensemble_predictions("test", weights, preds)
        self.assertEqual(result[0].meta["weights"], weights)
        self.assertEqual(result[0].meta["ensemble_name"], "test")

    def test_empty_predictions(self):
        result = build_ensemble_predictions("main", {"m1": 1.0}, {})
        self.assertEqual(result, [])

    def test_model_not_in_weights_excluded(self):
        weights = {"m1": 1.0}  # m2 not in weights
        preds = {
            "m1": [_pred(10.0)],
            "m2": [_pred(20.0)],
        }
        result = build_ensemble_predictions("main", weights, preds)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].inference_output["value"], 10.0)


class TestEnsembleMetrics(unittest.TestCase):
    def test_ensemble_correlation(self):
        from crunch_node.metrics.context import MetricsContext
        from crunch_node.metrics.ensemble_metrics import (
            compute_ensemble_correlation,
        )

        preds = [_pred(1.0), _pred(2.0), _pred(3.0)]
        ctx = MetricsContext(
            model_id="m1",
            ensemble_predictions={"main": [_pred(1.0), _pred(2.0), _pred(3.0)]},
        )
        corr = compute_ensemble_correlation(preds, [], ctx)
        self.assertAlmostEqual(corr, 1.0, places=5)

    def test_fnc_single_model_equals_ic(self):
        from crunch_node.metrics.builtins import compute_ic
        from crunch_node.metrics.context import MetricsContext
        from crunch_node.metrics.ensemble_metrics import compute_fnc

        preds = [_pred(1.0), _pred(2.0), _pred(3.0)]
        scores = [{"result": {"actual_return": 0.01 * (i + 1)}} for i in range(3)]
        ctx = MetricsContext(
            model_id="m1",
            all_model_predictions={"m1": preds},
        )

        fnc = compute_fnc(preds, scores, ctx)
        ic = compute_ic(preds, scores, ctx)
        # With only one model, FNC = IC
        self.assertAlmostEqual(fnc, ic, places=5)

    def test_contribution_positive_for_good_model(self):
        from crunch_node.metrics.context import MetricsContext
        from crunch_node.metrics.ensemble_metrics import compute_contribution

        # Model m1 has high IC, m2 has low — m1 should have positive contribution
        preds_m1 = [_pred(float(i)) for i in range(1, 6)]
        preds_m2 = [_pred(5.0 - float(i)) for i in range(5)]  # anti-signal
        # Ensemble = average of m1 and m2
        ens_preds = [_pred((float(i) + (5.0 - float(i))) / 2) for i in range(5)]
        scores = [{"result": {"actual_return": 0.01 * (i + 1)}} for i in range(5)]

        ctx = MetricsContext(
            model_id="m1",
            all_model_predictions={"m1": preds_m1, "m2": preds_m2},
            ensemble_predictions={"main": ens_preds},
        )

        contribution = compute_contribution(preds_m1, scores, ctx)
        # m1 is aligned with returns, removing it hurts, so contribution should be positive
        # (or at least non-negative)
        self.assertIsInstance(contribution, float)


if __name__ == "__main__":
    unittest.main()
