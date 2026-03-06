"""Tests for PredictSink feed-to-prediction integration."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from crunch_node.feeds import FeedDataRecord
from crunch_node.services.feed_window import FeedWindow
from crunch_node.services.predict_sink import PredictSink


class TestPredictSink(unittest.TestCase):
    def setUp(self):
        self.predict_service = MagicMock()
        self.predict_service.run_once = AsyncMock()

        self.feed_window = FeedWindow(max_size=10)

        self.sink = PredictSink(
            predict_service=self.predict_service,
            feed_window=self.feed_window,
        )

    def test_on_record_updates_window(self):
        record = FeedDataRecord(
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
                "volume": 100,
            },
            metadata={},
        )

        asyncio.run(self.sink.on_record(record))

        result = self.feed_window.get_input("BTC")
        candles = result["candles_1m"]
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]["close"], 50000)

    def test_on_record_calls_predict_service(self):
        record = FeedDataRecord(
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
                "volume": 100,
            },
            metadata={},
        )

        asyncio.run(self.sink.on_record(record))

        self.predict_service.run_once.assert_called_once()

        call_kwargs = self.predict_service.run_once.call_args.kwargs
        self.assertIn("raw_input", call_kwargs)
        self.assertEqual(call_kwargs["raw_input"]["symbol"], "BTC")
        self.assertIn("candles_1m", call_kwargs["raw_input"])

    def test_on_record_includes_feed_timing(self):
        record = FeedDataRecord(
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
                "volume": 100,
            },
            metadata={},
        )

        asyncio.run(self.sink.on_record(record))

        call_kwargs = self.predict_service.run_once.call_args.kwargs
        self.assertIn("feed_timing", call_kwargs)
        self.assertIn("feed_received_us", call_kwargs["feed_timing"])
        self.assertIn("feed_normalized_us", call_kwargs["feed_timing"])

    def test_build_input_returns_correct_format(self):
        self.feed_window.append(
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
                    "volume": 100,
                },
                metadata={},
            )
        )

        raw_input = self.sink._build_input("BTC")

        self.assertEqual(raw_input["symbol"], "BTC")
        self.assertEqual(raw_input["asof_ts"], 1000)
        self.assertIsInstance(raw_input["candles_1m"], list)
        self.assertEqual(len(raw_input["candles_1m"]), 1)


if __name__ == "__main__":
    unittest.main()
