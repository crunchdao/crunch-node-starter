"""Tests for feed normalizers."""

import unittest
from datetime import UTC, datetime

from crunch_node.feeds import FeedDataRecord
from crunch_node.feeds.normalizers import (
    NORMALIZERS,
    CandleInput,
    TickInput,
    get_normalizer,
)
from crunch_node.feeds.normalizers.candle import CandleNormalizer
from crunch_node.feeds.normalizers.tick import TickNormalizer


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

        self.assertIsInstance(result, CandleInput)
        self.assertEqual(result.symbol, "BTC")
        self.assertEqual(result.asof_ts, 1000)
        self.assertEqual(len(result.candles_1m), 1)

        candle = result.candles_1m[0]
        self.assertEqual(candle.open, 49900)
        self.assertEqual(candle.high, 50100)
        self.assertEqual(candle.low, 49800)
        self.assertEqual(candle.close, 50000)
        self.assertEqual(candle.volume, 123.45)

    def test_normalize_skips_tick_kind(self):
        """CandleNormalizer only handles candles, skips ticks."""
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

        self.assertEqual(result.symbol, "BTC")
        self.assertEqual(result.asof_ts, 0)
        self.assertEqual(result.candles_1m, [])

    def test_normalize_empty_records(self):
        result = self.normalizer.normalize([], "BTC")

        self.assertEqual(result.symbol, "BTC")
        self.assertEqual(result.asof_ts, 0)
        self.assertEqual(result.candles_1m, [])

    def test_normalize_with_datetime_ts_event(self):
        """Test that normalizer handles datetime ts_event (from DB records)."""

        class MockDBRecord:
            source = "binance"
            subject = "BTC"
            kind = "candle"
            granularity = "1m"
            ts_event = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
            values = {"open": 50000, "high": 50100, "low": 49900, "close": 50050, "volume": 100}

        records = [MockDBRecord()]

        result = self.normalizer.normalize(records, "BTC")

        self.assertEqual(result.symbol, "BTC")
        self.assertEqual(len(result.candles_1m), 1)
        self.assertIsInstance(result.asof_ts, int)

    def test_normalize_skips_records_without_price(self):
        records = [
            FeedDataRecord(
                source="binance",
                subject="BTC",
                kind="candle",
                granularity="1m",
                ts_event=1000,
                values={},
                metadata={},
            )
        ]

        result = self.normalizer.normalize(records, "BTC")

        self.assertEqual(result.candles_1m, [])

    def test_normalize_multiple_candle_records(self):
        records = [
            FeedDataRecord(
                source="binance",
                subject="BTC",
                kind="candle",
                granularity="1m",
                ts_event=1000 + i * 60,
                values={"open": 50000, "high": 50100, "low": 49900, "close": 50000 + i * 100, "volume": 100},
                metadata={},
            )
            for i in range(5)
        ]

        result = self.normalizer.normalize(records, "BTC")

        self.assertEqual(len(result.candles_1m), 5)
        self.assertEqual(result.asof_ts, 1000 + 4 * 60)
        self.assertEqual(result.candles_1m[0].close, 50000)
        self.assertEqual(result.candles_1m[4].close, 50400)

    def test_model_dump_produces_dict(self):
        records = [
            FeedDataRecord(
                source="binance",
                subject="BTC",
                kind="candle",
                granularity="1m",
                ts_event=1000,
                values={"open": 50000, "high": 50100, "low": 49900, "close": 50000, "volume": 100},
                metadata={},
            )
        ]

        result = self.normalizer.normalize(records, "BTC")
        dumped = result.model_dump()

        self.assertIsInstance(dumped, dict)
        self.assertEqual(dumped["symbol"], "BTC")
        self.assertEqual(dumped["candles_1m"][0]["close"], 50000)


class TestTickNormalizer(unittest.TestCase):
    def setUp(self):
        self.normalizer = TickNormalizer()

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

        self.assertIsInstance(result, TickInput)
        self.assertEqual(result.symbol, "BTC")
        self.assertEqual(result.asof_ts, 1000)
        self.assertEqual(len(result.ticks), 1)

        tick = result.ticks[0]
        self.assertEqual(tick.ts, 1000)
        self.assertEqual(tick.price, 50000)

    def test_normalize_skips_candle_kind(self):
        """TickNormalizer only handles ticks, skips candles."""
        records = [
            FeedDataRecord(
                source="binance",
                subject="BTC",
                kind="candle",
                granularity="1m",
                ts_event=1000,
                values={"open": 50000, "high": 50100, "low": 49900, "close": 50000, "volume": 100},
                metadata={},
            )
        ]

        result = self.normalizer.normalize(records, "BTC")

        self.assertEqual(result.symbol, "BTC")
        self.assertEqual(result.asof_ts, 0)
        self.assertEqual(result.ticks, [])

    def test_normalize_multiple_ticks(self):
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

        self.assertEqual(len(result.ticks), 5)
        self.assertEqual(result.asof_ts, 1004)
        self.assertEqual(result.ticks[0].price, 50000)
        self.assertEqual(result.ticks[4].price, 50400)

    def test_output_type_is_tick_input(self):
        self.assertEqual(self.normalizer.output_type, TickInput)


class TestNormalizerRegistry(unittest.TestCase):
    def test_registry_contains_candle(self):
        self.assertIn("candle", NORMALIZERS)

    def test_registry_contains_tick(self):
        self.assertIn("tick", NORMALIZERS)

    def test_get_normalizer_returns_default(self):
        normalizer = get_normalizer()
        self.assertIsInstance(normalizer, CandleNormalizer)

    def test_get_normalizer_returns_candle(self):
        normalizer = get_normalizer("candle")
        self.assertIsInstance(normalizer, CandleNormalizer)

    def test_get_normalizer_returns_tick(self):
        normalizer = get_normalizer("tick")
        self.assertIsInstance(normalizer, TickNormalizer)

    def test_get_normalizer_unknown_raises_error(self):
        with self.assertRaises(KeyError):
            get_normalizer("unknown")

    def test_get_normalizer_none_returns_default(self):
        normalizer = get_normalizer(None)
        self.assertIsInstance(normalizer, CandleNormalizer)

    def test_output_type_is_candle_input(self):
        normalizer = get_normalizer("candle")
        self.assertEqual(normalizer.output_type, CandleInput)

    def test_output_type_is_tick_input(self):
        normalizer = get_normalizer("tick")
        self.assertEqual(normalizer.output_type, TickInput)


if __name__ == "__main__":
    unittest.main()
