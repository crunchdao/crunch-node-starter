"""Tests for feed normalizers."""

import unittest
from datetime import UTC, datetime

from crunch_node.feeds import FeedDataRecord
from crunch_node.feeds.normalizers import NORMALIZERS, get_normalizer
from crunch_node.feeds.normalizers.candle import CandleNormalizer


class TestCandleNormalizer(unittest.TestCase):
    def setUp(self):
        self.normalizer = CandleNormalizer()

    def test_normalize_with_candle_kind(self):
        records = [
            FeedDataRecord(
                source="binance",
                subject="BTC",
                kind="candle",
                granularity="1m",
                ts_event=1000,
                values={
                    "open": 49900,
                    "high": 50100,
                    "low": 49800,
                    "close": 50000,
                    "volume": 123.45,
                },
                metadata={},
            )
        ]

        result = self.normalizer.normalize(records, "BTC")

        self.assertEqual(result["symbol"], "BTC")
        self.assertEqual(result["asof_ts"], 1000)
        self.assertEqual(len(result["candles_1m"]), 1)

        candle = result["candles_1m"][0]
        self.assertEqual(candle["open"], 49900)
        self.assertEqual(candle["high"], 50100)
        self.assertEqual(candle["low"], 49800)
        self.assertEqual(candle["close"], 50000)
        self.assertEqual(candle["volume"], 123.45)

    def test_normalize_with_tick_kind(self):
        records = [
            FeedDataRecord(
                source="pyth",
                subject="BTC",
                kind="tick",
                granularity="1s",
                ts_event=1000,
                values={"price": 50000},
                metadata={},
            )
        ]

        result = self.normalizer.normalize(records, "BTC")

        self.assertEqual(result["symbol"], "BTC")
        self.assertEqual(result["asof_ts"], 1000)

        candle = result["candles_1m"][0]
        self.assertEqual(candle["open"], 50000)
        self.assertEqual(candle["high"], 50000)
        self.assertEqual(candle["low"], 50000)
        self.assertEqual(candle["close"], 50000)
        self.assertEqual(candle["volume"], 0.0)

    def test_normalize_empty_records(self):
        result = self.normalizer.normalize([], "BTC")

        self.assertEqual(result["symbol"], "BTC")
        self.assertEqual(result["asof_ts"], 0)
        self.assertEqual(result["candles_1m"], [])

    def test_normalize_with_datetime_ts_event(self):
        """Test that normalizer handles datetime ts_event (from DB records)."""

        class MockDBRecord:
            source = "pyth"
            subject = "BTC"
            kind = "tick"
            granularity = "1s"
            ts_event = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
            values = {"price": 50000}

        records = [MockDBRecord()]

        result = self.normalizer.normalize(records, "BTC")

        self.assertEqual(result["symbol"], "BTC")
        self.assertEqual(len(result["candles_1m"]), 1)
        self.assertIsInstance(result["asof_ts"], int)

    def test_normalize_skips_records_without_price(self):
        records = [
            FeedDataRecord(
                source="pyth",
                subject="BTC",
                kind="tick",
                granularity="1s",
                ts_event=1000,
                values={},
                metadata={},
            )
        ]

        result = self.normalizer.normalize(records, "BTC")

        self.assertEqual(result["candles_1m"], [])

    def test_normalize_multiple_records(self):
        records = [
            FeedDataRecord(
                source="pyth",
                subject="BTC",
                kind="tick",
                granularity="1s",
                ts_event=1000 + i,
                values={"price": 50000 + i * 100},
                metadata={},
            )
            for i in range(5)
        ]

        result = self.normalizer.normalize(records, "BTC")

        self.assertEqual(len(result["candles_1m"]), 5)
        self.assertEqual(result["asof_ts"], 1004)
        self.assertEqual(result["candles_1m"][0]["close"], 50000)
        self.assertEqual(result["candles_1m"][4]["close"], 50400)


class TestNormalizerRegistry(unittest.TestCase):
    def test_registry_contains_candle(self):
        self.assertIn("candle", NORMALIZERS)

    def test_get_normalizer_returns_default(self):
        normalizer = get_normalizer()
        self.assertIsInstance(normalizer, CandleNormalizer)

    def test_get_normalizer_returns_candle(self):
        normalizer = get_normalizer("candle")
        self.assertIsInstance(normalizer, CandleNormalizer)

    def test_get_normalizer_unknown_returns_default(self):
        normalizer = get_normalizer("unknown")
        self.assertIsInstance(normalizer, CandleNormalizer)

    def test_get_normalizer_none_returns_default(self):
        normalizer = get_normalizer(None)
        self.assertIsInstance(normalizer, CandleNormalizer)


if __name__ == "__main__":
    unittest.main()
