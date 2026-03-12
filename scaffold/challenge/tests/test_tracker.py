"""Tests for TrackerBase subject-keyed data storage."""

from __future__ import annotations

import pytest
from starter_challenge.cruncher import ModelBaseClass


class DummyTracker(ModelBaseClass):
    """Minimal implementation for testing."""

    def predict(
        self, subject: str, resolve_horizon_seconds: int, step_seconds: int
    ) -> dict:
        data = self._get_data(subject)
        return {"value": 1.0 if data else 0.0}


class TestFeedUpdateSubjectKeying:
    """feed_update() must store data per-subject so multi-asset works."""

    def test_stores_data_by_symbol(self):
        tracker = DummyTracker()
        tracker.feed_update({"symbol": "BTC", "price": 100})
        tracker.feed_update({"symbol": "ETH", "price": 50})

        assert tracker._get_data("BTC")["price"] == 100
        assert tracker._get_data("ETH")["price"] == 50

    def test_does_not_overwrite_other_subjects(self):
        tracker = DummyTracker()
        tracker.feed_update({"symbol": "BTC", "price": 100})
        tracker.feed_update({"symbol": "ETH", "price": 50})
        tracker.feed_update({"symbol": "ETH", "price": 55})

        assert tracker._get_data("BTC")["price"] == 100
        assert tracker._get_data("ETH")["price"] == 55

    def test_updates_same_subject(self):
        tracker = DummyTracker()
        tracker.feed_update({"symbol": "BTC", "price": 100})
        tracker.feed_update({"symbol": "BTC", "price": 200})

        assert tracker._get_data("BTC")["price"] == 200


class TestGetDataFallback:
    """_get_data() falls back to _default when no symbol match."""

    def test_falls_back_to_default_when_no_symbol(self):
        tracker = DummyTracker()
        tracker.feed_update({"price": 100})  # no symbol key

        assert tracker._get_data("BTC")["price"] == 100
        assert tracker._get_data("ANY")["price"] == 100

    def test_exact_match_takes_priority_over_default(self):
        tracker = DummyTracker()
        tracker.feed_update({"price": 999})  # stored as _default
        tracker.feed_update({"symbol": "BTC", "price": 100})

        assert tracker._get_data("BTC")["price"] == 100
        assert tracker._get_data("OTHER")["price"] == 999  # falls back to _default

    def test_returns_none_when_empty(self):
        tracker = DummyTracker()
        assert tracker._get_data("BTC") is None


class TestFeedUpdateEdgeCases:
    """Edge cases for feed_update() input."""

    def test_non_dict_data_stored_as_default(self):
        tracker = DummyTracker()
        tracker.feed_update("not a dict")  # type: ignore[arg-type]
        # Should not crash; stored under _default
        assert tracker._get_data("BTC") == "not a dict"

    def test_empty_symbol_string(self):
        tracker = DummyTracker()
        tracker.feed_update({"symbol": "", "price": 42})
        assert tracker._get_data("")["price"] == 42
        # Empty string is not "_default", so other subjects don't see it
        assert tracker._get_data("BTC") is None


class TestPredictBase:
    def test_not_implemented_on_base(self):
        tracker = ModelBaseClass()
        with pytest.raises(NotImplementedError):
            tracker.predict("BTC", 60, 15)
