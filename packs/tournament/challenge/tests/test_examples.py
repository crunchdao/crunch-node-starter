"""Tests for the example tournament trackers."""

from __future__ import annotations

import pytest

from starter_challenge.examples.median_price_tracker import MedianPriceTracker
from starter_challenge.examples.price_per_sqft_tracker import PricePerSqftTracker


SAMPLE_FEATURES = {
    "living_area_sqft": 2000.0,
    "bedrooms": 3.0,
    "bathrooms": 2.0,
    "latitude": 33.75,
    "longitude": -84.39,
}

EMPTY_FEATURES: dict[str, float] = {}


@pytest.fixture(params=[PricePerSqftTracker, MedianPriceTracker])
def tracker(request):
    return request.param()


class TestExampleContract:
    """Every example must satisfy the tournament prediction contract."""

    def test_returns_dict_with_prediction(self, tracker):
        result = tracker.predict(SAMPLE_FEATURES)
        assert isinstance(result, dict)
        assert "prediction" in result
        assert isinstance(result["prediction"], (int, float))

    def test_prediction_is_positive(self, tracker):
        result = tracker.predict(SAMPLE_FEATURES)
        assert result["prediction"] >= 0

    def test_empty_features(self, tracker):
        result = tracker.predict(EMPTY_FEATURES)
        assert isinstance(result, dict)
        assert "prediction" in result


class TestPricePerSqft:
    def test_scales_with_area(self):
        tracker = PricePerSqftTracker()
        small = tracker.predict({"living_area_sqft": 1000.0})
        large = tracker.predict({"living_area_sqft": 3000.0})
        assert large["prediction"] > small["prediction"]

    def test_zero_sqft_returns_zero(self):
        tracker = PricePerSqftTracker()
        result = tracker.predict({"living_area_sqft": 0.0})
        assert result["prediction"] == 0.0


class TestMedianPrice:
    def test_always_returns_same_value(self):
        tracker = MedianPriceTracker()
        a = tracker.predict(SAMPLE_FEATURES)
        b = tracker.predict(EMPTY_FEATURES)
        assert a["prediction"] == b["prediction"]
