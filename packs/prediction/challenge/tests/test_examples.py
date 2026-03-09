"""Tests for the example prediction trackers."""

from __future__ import annotations

import pytest
from starter_challenge.examples.contrarian_tracker import ContrarianTracker
from starter_challenge.examples.mean_reversion_tracker import MeanReversionTracker
from starter_challenge.examples.momentum_tracker import MomentumTracker


def _make_candles(closes: list[float], base_ts: int = 1700000000) -> list[dict]:
    return [
        {
            "ts": base_ts + i * 60,
            "open": c,
            "high": c + 5,
            "low": c - 5,
            "close": c,
            "volume": 100,
        }
        for i, c in enumerate(closes)
    ]


def _make_feed_data(subject: str, closes: list[float]) -> dict:
    return {
        "symbol": subject,
        "asof_ts": 1700000000 + len(closes) * 60,
        "candles_1m": _make_candles(closes),
    }


UPTREND_CLOSES = [40000 + i * 50 for i in range(10)]
DOWNTREND_CLOSES = [40000 - i * 50 for i in range(10)]
EMPTY_FEED = {"symbol": "BTC", "asof_ts": 0, "candles_1m": []}


@pytest.fixture(
    params=[
        MomentumTracker,
        MeanReversionTracker,
        ContrarianTracker,
    ]
)
def tracker(request):
    """Parametrize over all example trackers."""
    return request.param()


class TestExampleContract:
    """Every example must satisfy the prediction contract."""

    def test_returns_dict_with_value(self, tracker):
        tracker.feed_update(_make_feed_data("BTC", UPTREND_CLOSES))
        result = tracker.predict("BTC", resolve_horizon_seconds=60, step_seconds=15)
        assert isinstance(result, dict)
        assert "value" in result
        assert isinstance(result["value"], (int, float))

    def test_empty_data_returns_zero(self, tracker):
        tracker.feed_update(EMPTY_FEED)
        result = tracker.predict("BTC", resolve_horizon_seconds=60, step_seconds=15)
        assert result["value"] == 0.0

    def test_no_data_returns_zero(self, tracker):
        result = tracker.predict("BTC", resolve_horizon_seconds=60, step_seconds=15)
        assert result["value"] == 0.0

    def test_sparse_candles_does_not_crash(self, tracker):
        tracker.feed_update(_make_feed_data("BTC", [40000, 40010]))
        result = tracker.predict("BTC", resolve_horizon_seconds=60, step_seconds=15)
        assert isinstance(result["value"], (int, float))
        assert result["value"] == 0.0

    def test_single_candle(self, tracker):
        tracker.feed_update(_make_feed_data("BTC", [40000]))
        result = tracker.predict("BTC", resolve_horizon_seconds=60, step_seconds=15)
        assert result["value"] == 0.0


class TestMultiSubjectIsolation:
    """feed_update() data must be isolated per subject."""

    def test_btc_and_eth_produce_different_predictions(self, tracker):
        tracker.feed_update(_make_feed_data("BTC", UPTREND_CLOSES))
        tracker.feed_update(_make_feed_data("ETH", DOWNTREND_CLOSES))

        btc_pred = tracker.predict("BTC", 60, 15)
        eth_pred = tracker.predict("ETH", 60, 15)

        assert btc_pred["value"] != eth_pred["value"], (
            f"BTC and ETH values should differ but both are {btc_pred['value']}"
        )

    def test_updating_eth_does_not_change_btc(self, tracker):
        tracker.feed_update(_make_feed_data("BTC", UPTREND_CLOSES))
        btc_before = tracker.predict("BTC", 60, 15)["value"]

        tracker.feed_update(_make_feed_data("ETH", DOWNTREND_CLOSES))
        btc_after = tracker.predict("BTC", 60, 15)["value"]

        assert btc_before == btc_after

    def test_unknown_subject_returns_zero(self, tracker):
        tracker.feed_update(_make_feed_data("BTC", UPTREND_CLOSES))
        result = tracker.predict("SOL", 60, 15)
        assert result["value"] == 0.0
