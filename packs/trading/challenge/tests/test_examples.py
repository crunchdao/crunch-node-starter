"""Tests for the example trading signal trackers."""

from __future__ import annotations

import pytest
from starter_challenge.examples.breakout_tracker import BreakoutTracker
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
EMPTY_TICK = {"symbol": "BTCUSDT", "asof_ts": 0, "candles_1m": []}


@pytest.fixture(
    params=[
        MomentumTracker,
        MeanReversionTracker,
        BreakoutTracker,
    ]
)
def tracker(request):
    """Parametrize over all example trackers."""
    return request.param()


class TestExampleContract:
    """Every example must satisfy the trading signal contract."""

    def test_returns_dict_with_signal(self, tracker):
        tracker.feed_update(_make_feed_data("BTCUSDT", UPTREND_CLOSES))
        result = tracker.predict("BTCUSDT", resolve_horizon_seconds=60, step_seconds=15)
        assert isinstance(result, dict)
        assert "signal" in result
        assert isinstance(result["signal"], (int, float))

    def test_signal_in_valid_range(self, tracker):
        tracker.feed_update(_make_feed_data("BTCUSDT", UPTREND_CLOSES))
        result = tracker.predict("BTCUSDT", resolve_horizon_seconds=60, step_seconds=15)
        assert -1.0 <= result["signal"] <= 1.0

    def test_empty_data_returns_zero(self, tracker):
        tracker.feed_update(EMPTY_TICK)
        result = tracker.predict("BTCUSDT", resolve_horizon_seconds=60, step_seconds=15)
        assert result["signal"] == 0.0

    def test_no_data_returns_zero(self, tracker):
        result = tracker.predict("BTCUSDT", resolve_horizon_seconds=60, step_seconds=15)
        assert result["signal"] == 0.0

    def test_sparse_candles_does_not_crash(self, tracker):
        tracker.feed_update(_make_feed_data("BTCUSDT", [40000, 40010]))
        result = tracker.predict("BTCUSDT", resolve_horizon_seconds=60, step_seconds=15)
        assert isinstance(result["signal"], (int, float))
        assert result["signal"] == 0.0

    def test_single_candle(self, tracker):
        tracker.feed_update(_make_feed_data("BTCUSDT", [40000]))
        result = tracker.predict("BTCUSDT", resolve_horizon_seconds=60, step_seconds=15)
        assert result["signal"] == 0.0


class TestMultiSubjectIsolation:
    """feed_update() data must be isolated per subject."""

    def test_btc_and_eth_produce_different_predictions(self, tracker):
        tracker.feed_update(_make_feed_data("BTCUSDT", UPTREND_CLOSES))
        tracker.feed_update(_make_feed_data("ETHUSDT", DOWNTREND_CLOSES))

        btc_pred = tracker.predict("BTCUSDT", 60, 15)
        eth_pred = tracker.predict("ETHUSDT", 60, 15)

        assert btc_pred["signal"] != eth_pred["signal"], (
            f"BTC and ETH signals should differ but both are {btc_pred['signal']}"
        )

    def test_updating_eth_does_not_change_btc(self, tracker):
        tracker.feed_update(_make_feed_data("BTCUSDT", UPTREND_CLOSES))
        btc_before = tracker.predict("BTCUSDT", 60, 15)["signal"]

        tracker.feed_update(_make_feed_data("ETHUSDT", DOWNTREND_CLOSES))
        btc_after = tracker.predict("BTCUSDT", 60, 15)["signal"]

        assert btc_before == btc_after

    def test_unknown_subject_returns_zero(self, tracker):
        tracker.feed_update(_make_feed_data("BTCUSDT", UPTREND_CLOSES))
        result = tracker.predict("SOLUSDT", 60, 15)
        assert result["signal"] == 0.0
