"""Tests for the generic MongoDB feed provider."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from coordinator_node.feeds.contracts import FeedFetchRequest, FeedSubscription
from coordinator_node.feeds.providers.mongodb import (
    _AUTH_ERROR_CODES,
    _CHANGE_STREAM_UNSUPPORTED_CODES,
    MongoDBFeed,
    _doc_to_record,
    _is_transient_error,
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
        "listen_mode": "changestream",
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


class TestDocToRecordBsonTypes(unittest.TestCase):
    """Test that non-JSON BSON types are safely converted to strings."""

    def test_non_json_types_converted_to_string(self):
        """Simulate BSON types like ObjectId by using a custom class."""

        class FakeObjectId:
            def __str__(self):
                return "507f1f77bcf86cd799439011"

        doc = {
            "mint": "TokenABC",
            "blockTime": 1700000000,
            "ref_id": FakeObjectId(),
            "price": 0.05,
            "name": "test",
            "count": 42,
            "active": True,
            "tags": ["a", "b"],
            "meta": {"k": "v"},
            "empty": None,
        }
        record = _doc_to_record(
            doc,
            subject_field="mint",
            timestamp_field="blockTime",
            kind="event",
            granularity="event",
        )
        assert record is not None
        # Non-JSON type should be stringified
        assert record.values["ref_id"] == "507f1f77bcf86cd799439011"
        # JSON-safe types should pass through as-is
        assert record.values["price"] == 0.05
        assert record.values["name"] == "test"
        assert record.values["count"] == 42
        assert record.values["active"] is True
        assert record.values["tags"] == ["a", "b"]
        assert record.values["meta"] == {"k": "v"}
        assert record.values["empty"] is None

    def test_nested_bson_types_converted(self):
        """BSON types inside lists and dicts should also be stringified."""

        class FakeObjectId:
            def __str__(self):
                return "abc123"

        doc = {
            "mint": "TokenABC",
            "blockTime": 1700000000,
            "refs": [FakeObjectId(), "normal"],
            "nested": {"id": FakeObjectId(), "count": 5},
        }
        record = _doc_to_record(
            doc,
            subject_field="mint",
            timestamp_field="blockTime",
            kind="event",
            granularity="event",
        )
        assert record is not None
        assert record.values["refs"] == ["abc123", "normal"]
        assert record.values["nested"] == {"id": "abc123", "count": 5}


class TestDocToRecordDottedPaths(unittest.TestCase):
    """Test that dotted field paths traverse nested documents."""

    def test_dotted_subject_field(self):
        doc = {"data": {"mint": "TokenABC"}, "blockTime": 1700000000}
        record = _doc_to_record(
            doc,
            subject_field="data.mint",
            timestamp_field="blockTime",
            kind="event",
            granularity="event",
        )
        assert record is not None
        assert record.subject == "TokenABC"

    def test_dotted_timestamp_field(self):
        doc = {"mint": "TokenABC", "meta": {"ts": 1700000000}}
        record = _doc_to_record(
            doc,
            subject_field="mint",
            timestamp_field="meta.ts",
            kind="event",
            granularity="event",
        )
        assert record is not None
        assert record.ts_event == 1700000000

    def test_missing_nested_path_returns_none(self):
        doc = {"mint": "TokenABC", "blockTime": 1700000000}
        record = _doc_to_record(
            doc,
            subject_field="data.mint",
            timestamp_field="blockTime",
            kind="event",
            granularity="event",
        )
        assert record is None


class TestMongoDBFeedFactory(unittest.TestCase):
    def test_build_returns_instance(self):
        feed = build_mongodb_feed(_make_settings())
        assert isinstance(feed, MongoDBFeed)


class TestListenMode(unittest.TestCase):
    def test_changestream_mode_accepted(self):
        feed = MongoDBFeed(_make_settings(listen_mode="changestream"))
        assert feed._listen_mode == "changestream"

    def test_poll_mode_accepted(self):
        feed = MongoDBFeed(_make_settings(listen_mode="poll"))
        assert feed._listen_mode == "poll"

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError) as ctx:
            MongoDBFeed(_make_settings(listen_mode="auto"))
        assert "listen_mode" in str(ctx.exception)

    def test_default_mode_is_changestream(self):
        # When listen_mode is not set, default to changestream
        settings = _make_settings()
        settings.options.pop("listen_mode", None)
        # Re-create without listen_mode key
        opts = dict(settings.options)
        opts.pop("listen_mode", None)
        feed = MongoDBFeed(FeedSettings(provider="mongodb", options=opts))
        assert feed._listen_mode == "changestream"


class TestErrorClassification(unittest.TestCase):
    """Test that errors are classified correctly for retry vs crash."""

    def test_unsupported_codes_are_known(self):
        """Verify all expected 'not supported' codes are in the set."""
        assert 40573 in _CHANGE_STREAM_UNSUPPORTED_CODES  # standalone
        assert 40324 in _CHANGE_STREAM_UNSUPPORTED_CODES  # old MongoDB
        assert 303 in _CHANGE_STREAM_UNSUPPORTED_CODES  # DocumentDB
        assert 115 in _CHANGE_STREAM_UNSUPPORTED_CODES  # CosmosDB
        assert 160 in _CHANGE_STREAM_UNSUPPORTED_CODES  # CosmosDB variant

    def test_auth_codes_are_known(self):
        assert 13 in _AUTH_ERROR_CODES  # Unauthorized
        assert 18 in _AUTH_ERROR_CODES  # AuthenticationFailed

    def test_transient_error_detection(self):
        """OperationFailure with unknown codes should be treated as transient."""
        try:
            from pymongo.errors import (
                ConnectionFailure,
                OperationFailure,
                ServerSelectionTimeoutError,
            )

            # Connection failures are transient
            assert _is_transient_error(ConnectionFailure("lost connection"))
            assert _is_transient_error(ServerSelectionTimeoutError("timeout"))

            # OperationFailure with unknown code is transient
            assert _is_transient_error(OperationFailure("cursor lost", code=999))

            # "Not supported" codes are NOT transient
            assert not _is_transient_error(
                OperationFailure("no changestream", code=40573)
            )

            # Auth codes are NOT transient
            assert not _is_transient_error(OperationFailure("unauthorized", code=13))
        except ImportError:
            self.skipTest("pymongo not installed")


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


class TestFieldNameValidation(unittest.TestCase):
    def test_valid_simple_field(self):
        # Should not raise
        MongoDBFeed(_make_settings(subject_field="mint"))

    def test_valid_dotted_field(self):
        # Nested paths like "data.price" are valid
        MongoDBFeed(_make_settings(subject_field="data.mint"))

    def test_valid_hyphenated_field(self):
        # Hyphens are common in MongoDB field names (block-time, token-address)
        MongoDBFeed(_make_settings(subject_field="block-time"))
        MongoDBFeed(_make_settings(timestamp_field="created-at"))

    def test_invalid_field_with_dollar(self):
        with self.assertRaises(ValueError) as ctx:
            MongoDBFeed(_make_settings(subject_field="$gt"))
        assert "subject_field" in str(ctx.exception)

    def test_invalid_field_with_braces(self):
        with self.assertRaises(ValueError) as ctx:
            MongoDBFeed(_make_settings(subject_field="field{inject}"))
        assert "subject_field" in str(ctx.exception)

    def test_invalid_timestamp_field(self):
        with self.assertRaises(ValueError) as ctx:
            MongoDBFeed(_make_settings(timestamp_field="$where"))
        assert "timestamp_field" in str(ctx.exception)

    def test_empty_field_rejected(self):
        with self.assertRaises(ValueError):
            MongoDBFeed(_make_settings(subject_field=""))


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
        # Numeric timestamps are preserved as-is for correct BSON type comparison
        assert isinstance(wm, int)
        assert wm == 1700000000

    def test_float_timestamp_watermark(self):
        from coordinator_node.feeds.providers.mongodb import _to_watermark

        wm = _to_watermark(1700000000.5)
        assert isinstance(wm, float)
        assert wm == 1700000000.5

    def test_none_for_unsupported_type(self):
        from coordinator_node.feeds.providers.mongodb import _to_watermark

        assert _to_watermark("not-a-timestamp") is None
        assert _to_watermark(None) is None


if __name__ == "__main__":
    unittest.main()
