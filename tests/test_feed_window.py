"""Tests for FeedWindow in-memory rolling window."""

import unittest
from unittest.mock import MagicMock

from crunch_node.feeds import FeedDataRecord
from crunch_node.feeds.normalizers import get_normalizer
from crunch_node.services.feed_window import FeedWindow


class TestFeedWindow(unittest.TestCase):
    def test_append_and_get_input_with_ticks(self):
        window = FeedWindow(max_size=3, normalizer=get_normalizer("tick"))

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

        for record in records:
            window.append(record)

        result = window.get_input("BTC")
        ticks = result["ticks"]

        self.assertEqual(len(ticks), 3)
        self.assertEqual(ticks[0]["ts"], 1002)
        self.assertEqual(ticks[2]["ts"], 1004)
        self.assertEqual(ticks[2]["price"], 50400)
        self.assertEqual(result["symbol"], "BTC")
        self.assertEqual(result["asof_ts"], 1004)

    def test_get_latest_ts(self):
        window = FeedWindow(max_size=10)

        window.append(
            FeedDataRecord(
                source="pyth",
                subject="BTC",
                kind="tick",
                granularity="1s",
                ts_event=12345,
                values={"price": 50000},
                metadata={},
            )
        )

        self.assertEqual(window.get_latest_ts("BTC"), 12345)
        self.assertEqual(window.get_latest_ts("ETH"), 0)

    def test_separate_windows_per_subject(self):
        window = FeedWindow(max_size=10, normalizer=get_normalizer("tick"))

        window.append(
            FeedDataRecord(
                source="pyth",
                subject="BTC",
                kind="tick",
                granularity="1s",
                ts_event=1000,
                values={"price": 50000},
                metadata={},
            )
        )
        window.append(
            FeedDataRecord(
                source="pyth",
                subject="ETH",
                kind="tick",
                granularity="1s",
                ts_event=1001,
                values={"price": 3000},
                metadata={},
            )
        )

        btc_result = window.get_input("BTC")
        eth_result = window.get_input("ETH")

        self.assertEqual(len(btc_result["ticks"]), 1)
        self.assertEqual(len(eth_result["ticks"]), 1)
        self.assertEqual(btc_result["ticks"][0]["price"], 50000)
        self.assertEqual(eth_result["ticks"][0]["price"], 3000)

    def test_tick_format_for_tick_data(self):
        window = FeedWindow(max_size=10, normalizer=get_normalizer("tick"))

        window.append(
            FeedDataRecord(
                source="pyth",
                subject="BTC",
                kind="tick",
                granularity="1s",
                ts_event=1000,
                values={"price": 50000},
                metadata={},
            )
        )

        result = window.get_input("BTC")
        tick = result["ticks"][0]

        self.assertEqual(tick["ts"], 1000)
        self.assertEqual(tick["price"], 50000)

    def test_candle_format_for_candle_data(self):
        window = FeedWindow(max_size=10)

        window.append(
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
        )

        result = window.get_input("BTC")
        candle = result["candles_1m"][0]

        self.assertEqual(candle["open"], 49900)
        self.assertEqual(candle["high"], 50100)
        self.assertEqual(candle["low"], 49800)
        self.assertEqual(candle["close"], 50000)
        self.assertEqual(candle["volume"], 123.45)

    def test_load_from_db(self):
        window = FeedWindow(max_size=10, normalizer=get_normalizer("tick"))

        mock_record = MagicMock()
        mock_record.source = "pyth"
        mock_record.subject = "BTC"
        mock_record.kind = "tick"
        mock_record.granularity = "1s"
        mock_record.ts_event.timestamp.return_value = 1000
        mock_record.values = {"price": 50000}
        mock_record.meta = {}

        mock_repo = MagicMock()
        mock_repo.fetch_records.return_value = [mock_record]

        mock_settings = MagicMock()
        mock_settings.subjects = ("BTC",)
        mock_settings.source = "pyth"
        mock_settings.kind = "tick"
        mock_settings.granularity = "1s"

        window.load_from_db(mock_repo, mock_settings)

        result = window.get_input("BTC")
        self.assertEqual(len(result["ticks"]), 1)

    def test_get_input_empty_subject(self):
        window = FeedWindow(max_size=10)

        result = window.get_input("UNKNOWN")

        self.assertEqual(result["symbol"], "UNKNOWN")
        self.assertEqual(result["asof_ts"], 0)
        self.assertEqual(result["candles_1m"], [])


if __name__ == "__main__":
    unittest.main()
