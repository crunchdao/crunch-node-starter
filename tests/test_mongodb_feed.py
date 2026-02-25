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
    # All required options must be provided — no silent defaults
    opts = {
        "mongodb_uri": "mongodb://localhost:27017",
        "database": "testdb",
        "collection": "events",
        "timestamp_field": "blockTime",
        "subject_field": "mint",
        "poll_seconds": "1",
        "inserted_at_field": "insertedAt",
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

    def test_builtin_kinds_accepted(self):
        for kind in ("tick", "candle", "depth", "funding"):
            req = FeedFetchRequest(subjects=("test",), kind=kind, granularity="1m")
            assert req.kind == kind


class TestRequiredSettings(unittest.TestCase):
    def test_missing_required_option_raises(self):
        # mongodb_uri is required — omitting it should raise
        settings = FeedSettings(
            provider="mongodb",
            options={
                "database": "testdb",
                "collection": "events",
                "timestamp_field": "blockTime",
                "subject_field": "mint",
            },
        )
        with self.assertRaises(ValueError):
            MongoDBFeed(settings)

    def test_missing_subject_field_raises(self):
        settings = FeedSettings(
            provider="mongodb",
            options={
                "mongodb_uri": "mongodb://localhost:27017",
                "database": "testdb",
                "collection": "events",
                "timestamp_field": "blockTime",
            },
        )
        with self.assertRaises(ValueError):
            MongoDBFeed(settings)


class TestRedactUri(unittest.TestCase):
    def test_redacts_credentials(self):
        from coordinator_node.feeds.providers.mongodb import _redact_uri

        assert "pass" not in _redact_uri("mongodb://user:pass@host:27017/db")
        assert "***" in _redact_uri("mongodb://user:pass@host:27017/db")

    def test_no_credentials_unchanged(self):
        from coordinator_node.feeds.providers.mongodb import _redact_uri

        uri = "mongodb://host:27017/db"
        assert _redact_uri(uri) == uri


class TestWatermarkConversion(unittest.TestCase):
    def test_datetime_watermark(self):
        from coordinator_node.feeds.providers.mongodb import _to_watermark

        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert _to_watermark(dt) == dt

    def test_int_timestamp_watermark(self):
        from coordinator_node.feeds.providers.mongodb import _to_watermark

        wm = _to_watermark(1700000000)
        assert isinstance(wm, datetime)
        assert int(wm.timestamp()) == 1700000000

    def test_float_timestamp_watermark(self):
        from coordinator_node.feeds.providers.mongodb import _to_watermark

        wm = _to_watermark(1700000000.5)
        assert isinstance(wm, datetime)

    def test_none_for_unsupported_type(self):
        from coordinator_node.feeds.providers.mongodb import _to_watermark

        assert _to_watermark("not-a-timestamp") is None
        assert _to_watermark(None) is None


if __name__ == "__main__":
    unittest.main()
