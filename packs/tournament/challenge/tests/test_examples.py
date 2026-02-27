"""Tests for the example tournament trackers."""

from __future__ import annotations

import pytest
from starter_challenge.examples.contrarian_tracker import ContrarianTracker
from starter_challenge.examples.feature_momentum_tracker import FeatureMomentumTracker
from starter_challenge.examples.linear_combo_tracker import LinearComboTracker


def _make_feature_tick(subject: str, features: dict[str, float]) -> dict:
    return {
        "symbol": subject,
        "asof_ts": 1700000000,
        "round_id": 1,
        "features": features,
    }


def _make_candle_tick(subject: str, closes: list[float]) -> dict:
    return {
        "symbol": subject,
        "asof_ts": 1700000000 + len(closes) * 60,
        "round_id": 1,
        "features": {},
        "candles_1m": [
            {
                "ts": 1700000000 + i * 60,
                "open": c,
                "high": c + 5,
                "low": c - 5,
                "close": c,
                "volume": 100,
            }
            for i, c in enumerate(closes)
        ],
    }


SAMPLE_FEATURES = {"momentum": 0.02, "volatility": -0.01, "trend": 0.015}
EMPTY_FEATURES = {}


@pytest.fixture(
    params=[
        FeatureMomentumTracker,
        LinearComboTracker,
        ContrarianTracker,
    ]
)
def tracker(request):
    """Parametrize over all example trackers."""
    return request.param()


class TestExampleContract:
    """Every example must satisfy the tournament prediction contract."""

    def test_returns_dict_with_prediction(self, tracker):
        tracker.tick(_make_feature_tick("BTC", SAMPLE_FEATURES))
        result = tracker.predict("BTC", resolve_horizon_seconds=3600, step_seconds=300)
        assert isinstance(result, dict)
        assert "prediction" in result
        assert isinstance(result["prediction"], (int, float))

    def test_empty_features_returns_zero(self, tracker):
        tracker.tick(_make_feature_tick("BTC", EMPTY_FEATURES))
        result = tracker.predict("BTC", resolve_horizon_seconds=3600, step_seconds=300)
        assert result["prediction"] == 0.0

    def test_no_tick_returns_zero(self, tracker):
        result = tracker.predict("BTC", resolve_horizon_seconds=3600, step_seconds=300)
        assert result["prediction"] == 0.0


class TestMultiSubjectIsolation:
    """tick() data must be isolated per subject."""

    def test_different_subjects_isolated(self, tracker):
        tracker.tick(_make_feature_tick("BTC", {"momentum": 0.05, "trend": 0.03}))
        tracker.tick(_make_feature_tick("ETH", {"momentum": -0.05, "trend": -0.03}))

        btc_pred = tracker.predict("BTC", 3600, 300)
        eth_pred = tracker.predict("ETH", 3600, 300)

        # LinearComboTracker and ContrarianTracker will produce different
        # results for opposite feature signs
        assert btc_pred["prediction"] != eth_pred["prediction"], (
            f"BTC and ETH predictions should differ but both are {btc_pred['prediction']}"
        )

    def test_unknown_subject_returns_zero(self, tracker):
        tracker.tick(_make_feature_tick("BTC", SAMPLE_FEATURES))
        result = tracker.predict("SOL", 3600, 300)
        assert result["prediction"] == 0.0
