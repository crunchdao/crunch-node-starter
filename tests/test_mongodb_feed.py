"""Tests for the generic MongoDB feed provider."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from coordinator_node.feeds.contracts import FeedFetchRequest, FeedSubscription
from coordinator_node.feeds.providers.mongodb import (
    MongoDBFeed,
    _doc_to_record,
    build_mongodb_feed,
)
from coordinator_node.feeds.registry import FeedSettings


def _make_settings(**overrides: str) -> FeedSettings:
    opts = {
        "mongodb_uri": "mongodb://localhost:27017",
        "database": "testdb",
        "collection": "events",
        "timestamp_field": "blockTime",
        "subject_field": "mint",
        "poll_seconds": "1",
    }
    opts.update(overrides)
    return FeedSettings(provider="mongodb", options=opts)


class TestDocToRecord(unittest.TestCase):
    def test_converts_basic_document(self):
        doc = {
            "_id": "abc123",
            "mint": "TokenABC",
            "blockTime": 1700000000,
            "price": 0.05,
        }
        record = _doc_to_record(
            doc,
            subject_field="mint",
            timestamp_field="blockTime",
            kind="event",
            granularity="event",
        )
        assert record is not None
        assert record.subject == "TokenABC"
        assert record.ts_event == 1700000000
        assert record.source == "mongodb"
        assert record.values["price"] == 0.05
        assert "_id" not in record.values

    def test_handles_datetime_timestamp(self):
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        doc = {"mint": "TokenABC", "blockTime": dt}
        record = _doc_to_record(
            doc,
            subject_field="mint",
            timestamp_field="blockTime",
            kind="event",
            granularity="event",
        )
        assert record is not None
        assert record.ts_event == int(dt.timestamp())

    def test_returns_none_for_missing_subject(self):
        doc = {"blockTime": 1700000000}
        record = _doc_to_record(
            doc,
            subject_field="mint",
            timestamp_field="blockTime",
            kind="event",
            granularity="event",
        )
        assert record is None

    def test_returns_none_for_missing_timestamp(self):
        doc = {"mint": "TokenABC"}
        record = _doc_to_record(
            doc,
            subject_field="mint",
            timestamp_field="blockTime",
            kind="event",
            granularity="event",
        )
        assert record is None


class TestMongoDBFeedFactory(unittest.TestCase):
    def test_build_returns_instance(self):
        feed = build_mongodb_feed(_make_settings())
        assert isinstance(feed, MongoDBFeed)


class TestFeedDataKindIsStr(unittest.TestCase):
    def test_custom_kind_accepted(self):
        req = FeedFetchRequest(subjects=("test",), kind="event", granularity="event")
        assert req.kind == "event"

        sub = FeedSubscription(
            subjects=("test",), kind="aggregate", granularity="event"
        )
        assert sub.kind == "aggregate"


if __name__ == "__main__":
    unittest.main()
