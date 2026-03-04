"""Tests for built-in metric implementations."""

from __future__ import annotations

import unittest
from typing import Any

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
from crunch_node.metrics.context import MetricsContext


def _pred(value: float) -> dict[str, Any]:
    return {"inference_output": {"value": value}}


def _score(value: float, actual_return: float = 0.0) -> dict[str, Any]:
    return {"result": {"value": value, "actual_return": actual_return}}


def _ctx(
    model_id: str = "m1",
    all_model_predictions: dict | None = None,
) -> MetricsContext:
    return MetricsContext(
        model_id=model_id,
        all_model_predictions=all_model_predictions or {},
    )


class TestIC(unittest.TestCase):
    def test_perfect_positive_correlation(self):
        preds = [_pred(1.0), _pred(2.0), _pred(3.0), _pred(4.0)]
        scores = [_score(0, 0.01), _score(0, 0.02), _score(0, 0.03), _score(0, 0.04)]
        ic = compute_ic(preds, scores, _ctx())
        self.assertAlmostEqual(ic, 1.0, places=5)

    def test_perfect_negative_correlation(self):
        preds = [_pred(4.0), _pred(3.0), _pred(2.0), _pred(1.0)]
        scores = [_score(0, 0.01), _score(0, 0.02), _score(0, 0.03), _score(0, 0.04)]
        ic = compute_ic(preds, scores, _ctx())
        self.assertAlmostEqual(ic, -1.0, places=5)

    def test_no_correlation(self):
        preds = [_pred(1.0), _pred(2.0)]
        scores = [_score(0, 0.02), _score(0, 0.01)]
        ic = compute_ic(preds, scores, _ctx())
        self.assertAlmostEqual(ic, -1.0, places=5)  # 2 points = perfect neg or pos

    def test_empty_returns_zero(self):
        self.assertAlmostEqual(compute_ic([], [], _ctx()), 0.0)

    def test_single_prediction_returns_zero(self):
        self.assertAlmostEqual(compute_ic([_pred(1.0)], [_score(0, 0.01)], _ctx()), 0.0)


class TestICSharp(unittest.TestCase):
    def test_consistent_ic_gives_high_sharpe(self):
        # Monotonically increasing — every chunk should have IC close to 1
        preds = [_pred(float(i)) for i in range(20)]
        scores = [_score(0, float(i) * 0.01) for i in range(20)]
        sharpe = compute_ic_sharpe(preds, scores, _ctx())
        # Perfectly consistent IC → inf or very high
        self.assertGreater(sharpe, 1.0)

    def test_too_few_predictions_returns_zero(self):
        preds = [_pred(1.0), _pred(2.0)]
        scores = [_score(0, 0.01), _score(0, 0.02)]
        self.assertAlmostEqual(compute_ic_sharpe(preds, scores, _ctx()), 0.0)


class TestMeanReturn(unittest.TestCase):
    def test_correct_predictions_positive_return(self):
        # Predict positive, actual positive
        preds = [_pred(1.0), _pred(1.0), _pred(1.0)]
        scores = [_score(0, 0.05), _score(0, 0.03), _score(0, 0.02)]
        ret = compute_mean_return(preds, scores, _ctx())
        self.assertGreater(ret, 0)

    def test_wrong_predictions_negative_return(self):
        # Predict positive, actual negative
        preds = [_pred(1.0), _pred(1.0)]
        scores = [_score(0, -0.05), _score(0, -0.03)]
        ret = compute_mean_return(preds, scores, _ctx())
        self.assertLess(ret, 0)

    def test_empty_returns_zero(self):
        self.assertAlmostEqual(compute_mean_return([], [], _ctx()), 0.0)


class TestHitRate(unittest.TestCase):
    def test_all_correct(self):
        preds = [_pred(1.0), _pred(-1.0), _pred(1.0)]
        scores = [_score(0, 0.05), _score(0, -0.03), _score(0, 0.01)]
        self.assertAlmostEqual(compute_hit_rate(preds, scores, _ctx()), 1.0)

    def test_all_wrong(self):
        preds = [_pred(1.0), _pred(-1.0)]
        scores = [_score(0, -0.05), _score(0, 0.03)]
        self.assertAlmostEqual(compute_hit_rate(preds, scores, _ctx()), 0.0)

    def test_half_correct(self):
        preds = [_pred(1.0), _pred(1.0), _pred(-1.0), _pred(-1.0)]
        scores = [_score(0, 0.01), _score(0, -0.01), _score(0, -0.01), _score(0, 0.01)]
        self.assertAlmostEqual(compute_hit_rate(preds, scores, _ctx()), 0.5)

    def test_empty_returns_zero(self):
        self.assertAlmostEqual(compute_hit_rate([], [], _ctx()), 0.0)


class TestMaxDrawdown(unittest.TestCase):
    def test_no_drawdown_all_positive(self):
        scores = [_score(1.0), _score(1.0), _score(1.0)]
        dd = compute_max_drawdown([], scores, _ctx())
        self.assertAlmostEqual(dd, 0.0)

    def test_drawdown_after_peak(self):
        scores = [_score(1.0), _score(1.0), _score(-3.0), _score(0.5)]
        dd = compute_max_drawdown([], scores, _ctx())
        # Peak at 2.0, trough at -1.0, drawdown = -3.0
        self.assertAlmostEqual(dd, -3.0)

    def test_empty_returns_zero(self):
        self.assertAlmostEqual(compute_max_drawdown([], [], _ctx()), 0.0)


class TestSortinoRatio(unittest.TestCase):
    def test_all_positive_returns(self):
        preds = [_pred(1.0)] * 5
        scores = [_score(0, 0.01)] * 5
        sortino = compute_sortino_ratio(preds, scores, _ctx())
        # All positive returns, no downside — should be very high or bounded
        self.assertGreater(sortino, 0)

    def test_mixed_returns(self):
        preds = [_pred(1.0), _pred(1.0), _pred(1.0), _pred(1.0)]
        scores = [_score(0, 0.05), _score(0, -0.02), _score(0, 0.03), _score(0, -0.01)]
        sortino = compute_sortino_ratio(preds, scores, _ctx())
        self.assertIsInstance(sortino, float)

    def test_too_few_returns_zero(self):
        self.assertAlmostEqual(
            compute_sortino_ratio([_pred(1.0)], [_score(0, 0.01)], _ctx()), 0.0
        )


class TestTurnover(unittest.TestCase):
    def test_constant_signal_zero_turnover(self):
        preds = [_pred(1.0), _pred(1.0), _pred(1.0)]
        self.assertAlmostEqual(compute_turnover(preds, [], _ctx()), 0.0)

    def test_varying_signal(self):
        preds = [_pred(1.0), _pred(2.0), _pred(0.0)]
        turnover = compute_turnover(preds, [], _ctx())
        # |2-1| + |0-2| = 1 + 2 = 3, avg = 1.5
        self.assertAlmostEqual(turnover, 1.5)

    def test_single_prediction_zero(self):
        self.assertAlmostEqual(compute_turnover([_pred(1.0)], [], _ctx()), 0.0)


class TestModelCorrelation(unittest.TestCase):
    def test_identical_models_correlation_one(self):
        my_preds = [_pred(1.0), _pred(2.0), _pred(3.0)]
        other_preds = [_pred(1.0), _pred(2.0), _pred(3.0)]
        ctx = _ctx(
            model_id="m1",
            all_model_predictions={
                "m1": my_preds,
                "m2": other_preds,
            },
        )
        corr = compute_model_correlation(my_preds, [], ctx)
        self.assertAlmostEqual(corr, 1.0, places=5)

    def test_opposite_models_correlation_negative(self):
        my_preds = [_pred(1.0), _pred(2.0), _pred(3.0)]
        other_preds = [_pred(3.0), _pred(2.0), _pred(1.0)]
        ctx = _ctx(
            model_id="m1",
            all_model_predictions={
                "m1": my_preds,
                "m2": other_preds,
            },
        )
        corr = compute_model_correlation(my_preds, [], ctx)
        self.assertAlmostEqual(corr, -1.0, places=5)

    def test_no_other_models_returns_zero(self):
        my_preds = [_pred(1.0), _pred(2.0)]
        ctx = _ctx(model_id="m1", all_model_predictions={"m1": my_preds})
        self.assertAlmostEqual(compute_model_correlation(my_preds, [], ctx), 0.0)

    def test_excludes_ensemble_models(self):
        my_preds = [_pred(1.0), _pred(2.0), _pred(3.0)]
        ctx = _ctx(
            model_id="m1",
            all_model_predictions={
                "m1": my_preds,
                "__ensemble_main__": [_pred(1.5), _pred(2.5), _pred(3.5)],
            },
        )
        corr = compute_model_correlation(my_preds, [], ctx)
        self.assertAlmostEqual(corr, 0.0)  # no real peers


if __name__ == "__main__":
    unittest.main()
